import sys, logging, asyncio, redis, json

redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

#redis_server.set(f"pwm_hot", json.dumps(0))
#redis_server.set(f"pwm_cold", json.dumps(0))
#redis_server.set(f"pwm_ambient", json.dumps(0.2))

print(redis_server.get(f"pwm_hot"))
print(redis_server.get(f"pwm_cold"))
print(redis_server.get(f"pwm_ambient"))


