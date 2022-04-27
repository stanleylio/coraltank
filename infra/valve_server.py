import logging, time, json, os, sys, redis
import RPi.GPIO as GPIO
sys.path.append('..')
from common import send_to_one_true_master
from common import beep as _beep


logger = logging.getLogger(__name__)

_valve_pin_map = {'cold':17, 'hot':22, 'ambient':27}


def valve_on(valve):
    logger.info(f"{valve} on")

    ts = time.time()
    
    GPIO.setup(_valve_pin_map[valve], GPIO.OUT)
    prev_state = GPIO.input(_valve_pin_map[valve])
    GPIO.output(_valve_pin_map[valve], GPIO.HIGH)
    redis_server.set(valve, json.dumps(True))

    # Using a queue (which consumes local resources) means you can't
    # defer edge detection to server, because now every update incurs
    # memory cost locally, whether there was indeed a valve op or not.
    if prev_state != GPIO.HIGH:
        try:
            send_to_one_true_master('valve', {'ts':ts, 'valve_id':valve, 'valve_state':True, }, )
        except:
            logger.exception('whatever')
    else:
        logger.debug('new=old, skip telemetry')

    # This might look like a reasonble place to send update via MQTT,
    # guarded by catch-all even. But the problem is some network
    # problems take significant amount of time before a timeout
    # exception is thrown. That holds up your valve operation and that
    # is a BIG NO NO.
    #try:
    #    send_to_one_true_master(blah......)
    #except:
        # You can never catch it all by listing the network problems one
        # by one. Caught the ConnectionRefusedError? Here comes a
        # socket.timeout.
        #logger.exception('?')

    '''Design notes, during the ongoing 20210915 HIMB One True Master
    Server outage.

    Waking up to a bleeping machine:

    One True Master Server went down -> send_to_the_one_true_master()
    calls throw socket.timeout exceptions instead of the expected
    ConnectionRefusedError -> XMLRPC valve_server is in another process
    so the failure is not visible in eztank.py -> ruined morning.

    Would catch-all work? No. Although this allows your process to keep
    going, the valve timing is thrown off by the network timeout (the
    MQTT publich calls still need to wait before giving up).

    You could revert back to the good old "publish to local RabbitMQ
    broker" and let it deal with the rest; here I'm trying something new
    with rq, which should achieve similar outcome.

    At the end of the day, it all boils down to the reliability of the
    components. A microcontroller with direct GPIO control is way more
    reliable than this mess because there are simply less pieces to
    fail, and I have better control of the electrical environment.
    RabbitMQ and Redis appear to mitigate the network problem only
    because them running on SD cards still manage to achieve lower
    failure rate of the HIMB network. There is no inherent
    "architectural superiority" in the broker/task queue design than a
    direct MQTT publish(). Not to mention the giant cans of worms these
    stateful services bring.
    '''


def valve_off(valve):
    logger.info(f"{valve} off")

    ts = time.time()
    
    GPIO.setup(_valve_pin_map[valve], GPIO.OUT)
    prev_state = GPIO.input(_valve_pin_map[valve])
    GPIO.output(_valve_pin_map[valve], GPIO.LOW)
    redis_server.set(valve, json.dumps(False))

    if prev_state != GPIO.LOW:
        try:
            send_to_one_true_master('valve', {'ts':ts, 'valve_id':valve, 'valve_state':False, }, )
        except:
            logger.exception('whatever')
    else:
        logger.debug('new=old, skip telemetry')


def beep(second):
    _beep(on=second, off=0)


def get_valve_state(valve):
    try:
        # "more real" but touches the hardware, and if the polarity
        # changes, yet one more place to remember to update...
        #return GPIO.HIGH == GPIO.input(_valve_pin_map[valve])
        # alternatively:
        return json.loads(redis_server.get(valve))
    except:
        # unknown, or whatever.
        return None


if '__main__' == __name__:

    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    logging.getLogger('pika').setLevel(logging.WARNING)

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

