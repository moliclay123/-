from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

auth_bp = Blueprint('auth', __name__)

AUTH_TOKEN_SALT = 'net-monitor-auth-token'


def get_token_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def generate_auth_token(username):
    return get_token_serializer().dumps({'username': username}, salt=AUTH_TOKEN_SALT)


def verify_auth_token(token):
    try:
        payload = get_token_serializer().loads(
            token,
            salt=AUTH_TOKEN_SALT,
            max_age=current_app.config.get('AUTH_TOKEN_EXPIRES', 86400)
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None

    if payload.get('username') != current_app.config.get('ADMIN_USERNAME'):
        return None

    return payload


def build_unauthorized_response():
    return jsonify({'code': 401, 'msg': '未登录或登录已过期'}), 401


def register_auth_guard(app):
    @app.before_request
    def require_auth():
        path = (request.path or '').rstrip('/') or request.path or ''

        if not path.startswith('/api/'):
            return None

        if request.method == 'OPTIONS':
            return None

        if path == '/api/auth/login':
            return None

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return build_unauthorized_response()

        token = auth_header.split(' ', 1)[1].strip()
        if not token:
            return build_unauthorized_response()

        payload = verify_auth_token(token)
        if payload is None:
            return build_unauthorized_response()

        g.current_user = payload.get('username')
        return None


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if (
        username != current_app.config.get('ADMIN_USERNAME') or
        password != current_app.config.get('ADMIN_PASSWORD')
    ):
        return jsonify({'code': 401, 'msg': '用户名或密码错误'}), 401

    expires_in = current_app.config.get('AUTH_TOKEN_EXPIRES', 86400)
    token = generate_auth_token(username)

    return jsonify({
        'code': 200,
        'msg': '登录成功',
        'data': {
            'token': token,
            'token_type': 'Bearer',
            'expires_in': expires_in,
            'username': username
        }
    })


@auth_bp.route('/me', methods=['GET'])
def me():
    current_user = getattr(g, 'current_user', None)
    if not current_user:
        return build_unauthorized_response()

    return jsonify({
        'code': 200,
        'msg': 'success',
        'data': {
            'username': current_user
        }
    })
