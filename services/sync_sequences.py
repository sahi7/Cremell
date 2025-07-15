import redis.asyncio as redis
from django.conf import settings
from services.sequences import SequencePool
from CRE.models import Branch, SequenceCounter

redis_client = redis.from_url(settings.REDIS_URL)
async def recover_sequences():
    for counter in await SequenceCounter.objects.all():
        pool = SequencePool(
            branch_id=counter.branch_id,
            sequence_type=counter.sequence_type,
            max_digits=counter.max_digits
        )
        await redis_client.set(
            pool.counter_key,
            counter.last_value
        )
        await pool.pregenerate_batch(100)


# Periodic sync to DB
@periodic_task(run_every=timedelta(minutes=5))
async def sync_sequence_state():
    for key in await redis_client.keys("seq_counter:*"):
        branch_id, seq_type = key.split(":")[1:3]
        value = await redis_client.get(key)
        await SequenceCounter.objects.aupdate_or_create(
            branch_id=branch_id,
            sequence_type=seq_type,
            defaults={'last_value': int(value)}
        )


# Monitoring Endpoint
async def sequence_status(request):
    stats = []
    for branch in await Branch.objects.all():
        pool = SequencePool(branch.id, "order")
        stats.append({
            "branch": branch.name,
            "available": await redis_client.llen(pool.pool_key),
            "last_value": await redis_client.get(pool.counter_key)
        })
    return Response(stats)