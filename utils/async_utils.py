
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

# Global executor for blocking calls
_executor = ThreadPoolExecutor(max_workers=10)

async def run_in_executor(func, *args, **kwargs):
    """
    Runs a blocking function in a separate thread to avoid blocking the asyncio loop.
    Usage: result = await run_in_executor(blocking_func, arg1, arg2)
    """
    loop = asyncio.get_running_loop()
    partial_func = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(_executor, partial_func)

class AsyncRateLimiter:
    """
    Simple Token Bucket Rate Limiter for Asyncio.
    """
    def __init__(self, rate_limit, period=1.0):
        self.rate_limit = rate_limit
        self.period = period
        self.tokens = rate_limit
        self.last_update = 0
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = asyncio.get_running_loop().time()
            time_passed = now - self.last_update
            self.tokens += time_passed * (self.rate_limit / self.period)
            if self.tokens > self.rate_limit:
                self.tokens = self.rate_limit
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.period / self.rate_limit)
                await asyncio.sleep(wait_time)
                self.tokens = 0
                self.last_update = asyncio.get_running_loop().time()
            
            self.tokens -= 1
