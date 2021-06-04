import logging, sys, subprocess


logger = logging.getLogger(__name__)


def reboot():
    subprocess.Popen(['reboot'])

def shutdown():
    subprocess.Popen('shutdown -h now'.split(' '))
    

if '__main__' == __name__:

    from xmlrpc.server import SimpleXMLRPCServer

    try:
        server = SimpleXMLRPCServer(('localhost', 8002), allow_none=True, logRequests=True)
        server.register_function(reboot, 'reboot')
        server.register_function(shutdown, 'shutdown')
        server.serve_forever()
    except KeyboardInterrupt:
        print('user interrupted')
    except:
        logger.exception('wut?')
