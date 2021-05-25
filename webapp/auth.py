from functools import wraps
from flask import request, Response
import logging, sys
sys.path.append('/home/pi')
from cred import cred


# need to add "WSGIPassAuthorization On" to apache site .conf file to make this work


def check_auth(userid, password):
    try:
        return 'pi' == userid and cred['webapp'] == password
    except Exception:
        logging.exception(f'userid={userid}, password={password}')
    return False


def authenticate():
    return Response(
        'bad credential',
        401,
        {'WWW-Authenticate':'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


if '__main__' == __name__:

    print(check_auth('x', 'a'))
    print(check_auth('pi', 'tanksystem210'))
    
