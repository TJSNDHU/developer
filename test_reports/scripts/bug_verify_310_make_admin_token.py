import os, time, pathlib
import jwt
from dotenv import dotenv_values
env = dotenv_values('/app/backend/.env')
secret = env.get('JWT_SECRET') or os.environ['JWT_SECRET']
admin_email = env.get('ADMIN_EMAIL') or 'admin@example.test'
now = int(time.time())
token = jwt.encode({
    'user_id': 'bug-verify-ui-admin',
    'email': admin_email,
    'is_admin': True,
    'iat': now,
    'jti': 'bugverify310uitoken',
    'exp': now + 1800,
}, secret, algorithm='HS256')
path = pathlib.Path('/app/test_reports/.bug_verify_310_admin_token')
path.write_text(token)
path.chmod(0o600)
print('token_file_created')
