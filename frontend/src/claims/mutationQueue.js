// Recoverable failures must not poison later evidence uploads. Each action
// reads the latest claim version only once earlier actions have completed.
export function createMutationQueue() {
  let tail = Promise.resolve();
  return action => {
    const next = tail.catch(() => {}).then(action);
    tail = next;
    return next;
  };
}
