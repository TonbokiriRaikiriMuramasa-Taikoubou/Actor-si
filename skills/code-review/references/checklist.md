# Code review checklist

## 1. Correctness
- [ ] Boundary values (zero items, one item, maximum size, empty string, null)
- [ ] Exception paths (does a failure leave state corrupted?)
- [ ] Concurrency and ordering guarantees in asynchronous work
- [ ] Reassignment or shared references inside loops (shallow copies)
- [ ] Time zones, rounding, currencies, and units

## 2. Security
- [ ] Is external input passed directly into a query, shell, or HTML context?
- [ ] Are authorization checks applied at every operation, not just at entry points?
- [ ] Are secrets hard-coded or written to logs?
- [ ] Can path construction escape via `..`?
- [ ] Are added dependencies justified, maintained, and free of known relevant vulnerabilities?

## 3. Resources
- [ ] Are files, sockets, and connections closed?
- [ ] Are there N+1 accesses or unbounded caches?
- [ ] Are retry counts and backoff bounded (no infinite retries)?

## 4. Operations
- [ ] Do failures produce logs that help identify the cause without exposing secrets?
- [ ] Are defaults safe for production?
- [ ] Is backward compatibility preserved (API, schema, and stored formats)?

## 5. Tests
- [ ] Are changed branches covered by tests?
- [ ] Do tests verify behavior rather than duplicate the implementation?
