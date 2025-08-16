import asyncio
from concurrent.futures import ThreadPoolExecutor
from django.db.transaction import Atomic
from asgiref.sync import sync_to_async
from django.db import connections

# Maximum number of concurrent transactions (tune based on DB/server capacity)
MAX_CONCURRENT_TRANSACTIONS = 10

# Pool of single-threaded executors
executor_pool = asyncio.Queue()
for _ in range(MAX_CONCURRENT_TRANSACTIONS):
    executor_pool.put_nowait(ThreadPoolExecutor(1))

class AsyncAtomicContextManager(Atomic):
    """Asynchronous atomic context manager with a pooled executor for high-load robustness."""

    async def __aenter__(self):
        print("TRANSACTION STARTED")
        # Get an executor from the pool
        self.executor = await executor_pool.get()
        # Start transaction in the assigned thread
        await sync_to_async(super().__enter__, thread_sensitive=False, executor=self.executor)()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        print(f"TRANSACTION ENDING: {exc_type}")
        try:
            # End transaction in the assigned thread
            # await sync_to_async(super().__exit__, thread_sensitive=False, executor=self.executor)(exc_type, exc_value, traceback)
            await sync_to_async(super().__exit__, thread_sensitive=False, executor=self.executor)(exc_type, exc_value, traceback)
            # Close connections in the assigned thread
            # await sync_to_async(self.close_connections, thread_sensitive=False, executor=self.executor)()
        finally:
            # Return executor to the pool
            await executor_pool.put(self.executor)

    async def run_in_context(self, fun, *args, **kwargs):
        """Execute a function within the transaction context."""
        if asyncio.iscoroutinefunction(fun):
            # For async functions, run ORM ops in the same thread as the transaction
            # return await sync_to_async(lambda: fun(*args, **kwargs), thread_sensitive=False, executor=self.executor)()
            return await fun(*args, **kwargs)
        else:
            # For sync functions, run directly in the executor
            future = self.executor.submit(fun, *args, **kwargs)
            return await asyncio.wrap_future(future)

    def close_connections(self):
        """Close all database connections for this thread."""
        for conn in connections.all():
            conn.close()

def aatomic(using=None, savepoint=True, durable=False):
    """Decorator to run a function in an atomic async context."""
    def decorator(fun):
        async def wrapper(*args, **kwargs):
            async with AsyncAtomicContextManager(using, savepoint, durable) as aacm:
                return await aacm.run_in_context(fun, *args, **kwargs)
        return wrapper
    return decorator

# Example usage
# @aatomic()
# async def example_function():
#     from myapp.models import MyModel
#     instance = await MyModel.objects.aget(id=1)
#     instance.some_field = "updated"
#     await instance.asave()
#     return instance