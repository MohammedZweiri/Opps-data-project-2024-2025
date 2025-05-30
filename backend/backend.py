import warnings
from copy import deepcopy
from functools import wraps

import marshmallow as ma
import requests
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import Flask
from flask.views import MethodView
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, get_jwt_identity, jwt_required
from flask_mysqldb import MySQL
from flask_smorest import Api, Blueprint, abort
from flask_smorest.error_handler import ErrorSchema
from marshmallow import validate

from config import Config

warnings.filterwarnings("ignore", message="Multiple schemas resolved to the name ")

ph = PasswordHasher()


def jwt_required_with_oas(*args, **kwargs):
    """Overwrite the jwt_required decorator to add openapi doc support."""

    # noinspection PyProtectedMember
    def decorator(func):
        @wraps(func)
        def wrapper(*f_args, **f_kwargs):
            return jwt_required(*args, **kwargs)(func)(*f_args, **f_kwargs)

        # Add the security information to the openapi doc
        wrapper._apidoc = deepcopy(getattr(func, "_apidoc", {}))
        wrapper._apidoc.setdefault("manual_doc", {})
        wrapper._apidoc["manual_doc"]["security"] = [{"Bearer Auth": []}]

        # Add the 401 response to the openapi doc
        wrapper._apidoc["manual_doc"].setdefault("responses", {})
        wrapper._apidoc["manual_doc"]["responses"][401] = {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "msg": {  # current_app.config["JWT_ERROR_MESSAGE_KEY"]
                                "type": "string",
                                "example": "Unauthorized",
                            }
                        },
                    }
                }
            },
        }

        return wrapper

    return decorator


def verify_recaptcha(token):
    """Verify reCAPTCHA token with Google API"""

    try:
        url = "https://www.google.com/recaptcha/api/siteverify"
        data = {"secret": app.config["RECAPTCHA_SECRET_KEY"], "response": token}
        response = requests.post(url, data=data).json()
        return response.get("success", False)

    except Exception as e:
        print(f"reCAPTCHA verification failed. Error: {e}")
        return None


class UserBaseSchema(ma.Schema):
    username = ma.fields.String(required=True, validate=validate.Length(max=20))

    password = ma.fields.String(load_only=True, required=True, validate=validate.Length(min=7, max=50))

    uid = ma.fields.Integer(dump_only=True, attribute="userID")
    role = ma.fields.String(dump_only=True)


class UserRegisterSchema(UserBaseSchema):
    email = ma.fields.Email(required=True, validate=validate.Length(max=50))


class UserLoginSchema(UserBaseSchema):
    recaptcha_token = ma.fields.String(load_only=True, required=True)

    email = ma.fields.Email(dump_only=True)
    access_token = ma.fields.String(dump_only=True)
    refresh_token = ma.fields.String(dump_only=True)


class PostSchema(ma.Schema):
    post_id = ma.fields.Integer(required=True, attribute="postID")
    forum_id = ma.fields.Integer(required=True, attribute="forumID")
    username = ma.fields.String(required=True, attribute="postName", validate=validate.Length(max=20))
    time = ma.fields.DateTime(required=True, attribute="postTime", format="%Y-%m-%d %H:%M:%S")
    text = ma.fields.String(required=True, attribute="postText", validate=validate.Length(max=512))


app = Flask(__name__)
app.config.from_object(Config)

CORS(app)
api = Api(app)
mysql = MySQL(app)
jwt = JWTManager(app)

users_bp = Blueprint("user", __name__, url_prefix="/api/user")
posts_bp = Blueprint("post", __name__, url_prefix="/api/post")


@users_bp.route("/register", endpoint="register")
class Users(MethodView):
    @users_bp.arguments(UserRegisterSchema, location="json")
    @users_bp.response(201, UserRegisterSchema)
    @users_bp.alt_response(409, schema=ErrorSchema)
    def post(self, args):
        """Create a new user"""
        email, name, password = args["email"], args["username"], args["password"]

        # Hash the password using Argon2
        hashed_password = ph.hash(password)

        with mysql.connection.cursor() as cursor:
            # Check if user's email already exists
            cursor.execute("SELECT 1 FROM Users WHERE email = %s", (email,))
            if cursor.fetchone():
                abort(409, message="Email already exists")

            # Check if user's username already exists
            cursor.execute("SELECT 1 FROM Users WHERE username = %s", (name,))
            if cursor.fetchone():
                abort(409, message="Username already exists")

            # Insert the new user
            cursor.execute(
                "INSERT INTO Users (email, password, username) VALUES (%s, %s, %s)",
                (email, hashed_password, name),
            )
            mysql.connection.commit()

            # Return the new user
            cursor.execute("SELECT * FROM Users WHERE username = %s", (name,))
            return cursor.fetchone()


@users_bp.route("/login", endpoint="login")
class UsersLogin(MethodView):
    @users_bp.arguments(UserLoginSchema, location="json")
    @users_bp.response(200, UserLoginSchema)
    @users_bp.alt_response(404, schema=ErrorSchema)
    @users_bp.alt_response(401, schema=ErrorSchema)
    def post(self, args):
        """Login"""
        username, password, recaptcha_token = args["username"], args["password"], args["recaptcha_token"]

        if not verify_recaptcha(recaptcha_token):
            abort(401, message="reCAPTCHA verification failed")

        with mysql.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM Users WHERE username = %s", (username,))

            if not (user := cursor.fetchone()):
                abort(404, message="User not found")

            try:
                ph.verify(user["password"], password)
            except VerifyMismatchError:
                abort(401, message="Incorrect password")

            # Rehash the password if needed
            if ph.check_needs_rehash(user["password"]):
                new_hash = ph.hash(password)
                cursor.execute("UPDATE Users SET password = %s WHERE username = %s", (new_hash, username))
                mysql.connection.commit()

        # Create access token and refresh token
        user["access_token"] = create_access_token(identity=user["username"])
        user["refresh_token"] = create_access_token(identity=user["username"], fresh=True)

        return user


@posts_bp.route("", endpoint="index")
class Posts(MethodView):
    @jwt_required_with_oas()
    @posts_bp.response(200, PostSchema(many=True))
    @posts_bp.alt_response(403, schema=ErrorSchema)
    def get(self):
        """Get all posts"""
        with mysql.connection.cursor() as cursor:
            cursor.execute(
                "SELECT P.postID, P.forumID, P.postName, P.postTime, P.postText, U.email "
                "FROM Posts P "
                "INNER JOIN Users U ON P.postName = U.username"
            )
            return list(cursor.fetchall())

    @jwt_required_with_oas()
    @posts_bp.arguments(PostSchema(only=("forum_id", "username", "time", "text")), location="json")
    @posts_bp.response(201)
    @posts_bp.alt_response(403, schema=ErrorSchema)
    def post(self, args):
        """Create a new post"""
        fid, name, time, text = (args["forumID"], args["postName"], args["postTime"], args["postText"])
        current_user = get_jwt_identity()

        if current_user != name:
            abort(403, message="You can only create posts with your own username")

        with mysql.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO Posts (forumID, postName, postTime, postText) VALUES (%s, %s, %s, %s)",
                (fid, name, time, text),
            )
            mysql.connection.commit()

    @jwt_required_with_oas()
    @posts_bp.arguments(PostSchema(only=("forum_id", "post_id", "username", "text")), location="json")
    @posts_bp.response(204)
    def put(self, args):
        """Update a post"""
        fid, pid, name, text = (args["forumID"], args["postID"], args["postName"], args["postText"])
        current_user = get_jwt_identity()

        if current_user != name:
            abort(403, message="You can only update posts with your own username")

        with mysql.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE Posts SET postText = %s WHERE forumID = %s AND postID = %s AND postName = %s",
                (text, fid, pid, name),
            )
            mysql.connection.commit()

    @jwt_required_with_oas()
    @posts_bp.arguments(PostSchema(only=("forum_id", "post_id", "username")), location="json")
    @posts_bp.response(204)
    @posts_bp.alt_response(403, schema=ErrorSchema)
    def delete(self, args):
        """Delete a post"""
        fid, pid, name = args["forumID"], args["postID"], args["postName"]
        current_user = get_jwt_identity()

        if current_user != name:
            abort(403, message="You can only delete posts with your own username")

        with mysql.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM Posts WHERE forumID = %s AND postID = %s AND postName = %s",
                (fid, pid, name),
            )
            mysql.connection.commit()


api.register_blueprint(users_bp)
api.register_blueprint(posts_bp)

print(app.url_map)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
    # app.run(host="127.0.0.1", port=80, debug=True)
