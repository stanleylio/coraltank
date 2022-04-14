import logging, time, json, os, sys, redis
import RPi.GPIO as GPIO
sys.path.append('..')
from common import send_to_the_one_true_master
sys.path.append('../..')
from node.drivers.beep import beep as _beep


logger = logging.getLogger(__name__)

_valve_pin_map = {'cold':17, 'hot':22, 'ambient':27}


def valve_on(valve):
    logger.info(f"{valve} on")
    
    GPIO.setup(_valve_pin_map[valve], GPIO.OUT)
    GPIO.output(_valve_pin_map[valve], GPIO.HIGH)
    redis_server.set(valve, json.dumps(True))
    try:
        topic_suffix = f"valve/{valve}/on"
        send_to_the_one_true_master(topic_suffix, {'ts':time.time(), })
    except ConnectionRefusedError:
        logger.warning('?')


def valve_off(valve):
    logger.info(f"{valve} off")
    
    GPIO.setup(_valve_pin_map[valve], GPIO.OUT)
    GPIO.output(_valve_pin_map[valve], GPIO.LOW)
    redis_server.set(valve, json.dumps(False))
    try:
        topic_suffix = f"valve/{valve}/off"
        send_to_the_one_true_master(topic_suffix, {'ts':time.time(), })
    except ConnectionRefusedError:
        logger.warning('?')


def beep(second):
    _beep(on=second, off=0)


def get_valve_state(valve):
    try:
        return json.loads(redis_server.get(valve))
    except:
        # unknown
        return None


if '__main__' == __name__:

    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    from xmlrpc.server import SimpleXMLRPCServer

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
    for k in _valve_pin_map:
        redis_server.delete(k)

    try:
        server = SimpleXMLRPCServer(('localhost', 8001), allow_none=True, logRequests=False)
        server.register_function(valve_on, 'valve_on')
        server.register_function(valve_off, 'valve_off')
        server.register_function(get_valve_state, 'get_valve_state')
        server.register_function(beep, 'beep')
        server.serve_forever()
    except KeyboardInterrupt:
        print('user interrupted')
    except:
        logger.exception('wut?')
    finally:
        for valve,pin in _valve_pin_map.items():
            redis_server.delete(valve)
            valve_off(valve)
            GPIO.cleanup(pin)

