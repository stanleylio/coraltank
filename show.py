# SL2021
import time, redis, json, sys


redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

while True:
    print('- - - - -')

    for k in redis_server.scan_iter():
#        if k.startswith(b'rq:'):
#            continue
        
        v = redis_server.get(k)
        ttl = redis_server.ttl(k)
#        if v is not None:
#            v = json.loads(v)
        print(f"{k} = {v} ({ttl})")

    # - - -
    
#    for k in redis_server.scan_iter():
#        if k.startswith(b'rq:'):
#            print(k)
#            #redis_server.delete(k)

    time.sleep(3)
