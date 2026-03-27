function getStreamName(prefix) {
    return `${prefix}:commands`;
}
export async function publishLaunch(redis, prefix, processId) {
    await redis.xadd(getStreamName(prefix), '*', 'type', 'launch', 'payload', JSON.stringify({ processId }));
}
export async function publishCancel(redis, prefix, processId) {
    await redis.xadd(getStreamName(prefix), '*', 'type', 'cancel', 'payload', JSON.stringify({ processId }));
}
export async function publishDismiss(redis, prefix, processId) {
    await redis.xadd(getStreamName(prefix), '*', 'type', 'dismiss', 'payload', JSON.stringify({ processId }));
}
export async function publishResync(redis, prefix, clean = false) {
    await redis.xadd(getStreamName(prefix), '*', 'type', 'resync', 'payload', JSON.stringify({ clean }));
}
//# sourceMappingURL=publisher.js.map