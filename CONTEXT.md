# FeedForger

FeedForger combines source feeds into named JSON feeds while preserving enough
origin and failure information to maintain those sources over time.

## Language

**Recipe**:
A named instruction for forging one feed group from one or more source feeds.
_Avoid_: Configuration, job

**Feed group**:
A published JSON Feed containing normalized items collected by one recipe.
_Avoid_: Channel, category

**Item**:
A dated article or update emitted by a source feed and normalized for publication.
_Avoid_: Entry, post

**Content**:
The normalized body, summary, image, authorship, tags, and source attribution
carried by an item, chosen from embedded feed material and fulfilled page material.
_Avoid_: Payload, extracted content

**Fulfill**:
Enrich an item that lacks substantial embedded content with content from its
original page.
_Avoid_: Hydrate, expand

**Failure report**:
A snapshot of consecutive retrieval failures and the latest error for every
source URL, used to identify sources that need attention or removal.
_Avoid_: Error log
