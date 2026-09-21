## Sanctions Delta Watch — live customer demonstration

### Three-minute walkthrough

1. **Problem (30 sec).** “Imagine a customer has a saved withdrawal destination. Its risk status can change after onboarding. How do we reconnect new intelligence to relationships we already know?” This is a hypothetical scenario; the supplied historical records do not prove a previously clean address later changed.
2. **Live signal (45 sec).** The address book checks automatically on opening and repeats 60 seconds after each completed scan while the tab is visible. Click **Screen customer address book** for an immediate refresh. Point to the Ethereum block and observation timestamp. “These are actual read-only calls to Chainalysis’s public sanctions oracle. The customers and relationships are synthetic.” Results can change; do not promise a fixed count.
3. **Customer context (30 sec).** Pick the saved withdrawal destination. “The oracle answers an address-level question. My application tells the reviewer why that address matters to this business.” Change its local review status. No transfer is blocked and no external ticket is created.
4. **Provenance (30 sec).** Click **Retrieve historical event**. Explain the original event date separately from today’s retrieval/check time. Open its explorer link. Export the evidence JSON.
5. **Value and discovery (45 sec).** “The value is connecting an intelligence signal to repeatable controls and a review trail. What is your address inventory? How often is it screened? Who owns a match? What evidence must you retain?” Discuss broader Address Screening, KYT and Reactor capabilities according to the customer’s needs.

### What is live

- Automatic visible-tab and manual `eth_call` to `isSanctioned(address)`, pinned to a specific Ethereum block.
- Historical receipt and block retrieval, including emitter and event decoding checks.
- Public RPC providers; no browser API key, wallet signature or transaction submission.
- Customer screening evidence export, plus local page-session review state.

### What requires production engineering

- Durable customer registry, server-side scheduling/event ingestion and replay checkpoints.
- Authentication, authorization, audit retention, idempotent case delivery and governed policy decisions.
- Provider service levels, monitoring, retry queues, finality/reorg handling and removals.
- Formal data handling and security review. This frontend pauses monitoring when hidden or closed. Use Pause/Resume to control automatic checks.

## Warnings

- Public RPC providers can fail, throttle or omit older receipts. Historical retrieval tries another provider. An unavailable result is not a negative result.
- Highlighted rows identify sanctions designations, not compromised keys or hacked wallets. Change indicators compare successful results in this page session; they are not proof of the exact designation time.
- A no-match result is limited to this oracle at the checked block. It is not proof of safety or complete compliance.
- Address queries are sent to public RPC operators. Use public demo addresses during the interview.
- Synthetic relationships are illustrative; severity and decisions are not Chainalysis risk ratings.
- The public sanctions oracle is not the paid Chainalysis REST API. Product links describe possible next discovery conversations.
