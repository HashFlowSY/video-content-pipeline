# Use user-ordered manual collections

Phase 3 forms a multi-Part MediaCollection only through a manual collection
session: the user submits public URLs in their intended presentation order and
closes the collection with `结束`. The supplied sequence becomes the
authoritative Part order, and the interface must remind the user to submit
links strictly in order; it does not infer an order or discover a playlist.
Before closure, entries receive only local format and duplicate checks. Closure
permits the batch's authorized network validation and source acquisition.

## Considered Options

- User-ordered manual collection: accepted because each source stays
  independently authorized and collection virtual time has an explicit,
  auditable order.
- Automatic page or playlist enumeration: rejected because page structure and
  ordering can be ambiguous and would introduce unrequested source discovery.
