# Restricted narrator protocol

## Purpose and trust boundary

The narrator broker is a model-independent interface between a trusted game
engine and an evidence-limited client. It owns private session paths and returns
only public game views, public report history, action receipts and deliberately
requested post-exercise debriefs.

The trusted host starts `submarine-command-narrator` with a private storage root
and connects its standard input and output to an LLM tool adapter. The narrator
receives an opaque session identifier, not a directory. The broker does not
provide network listening, session discovery, raw state, save export or
arbitrary file access.

Process separation alone does not remove the narrator's filesystem privileges.
For a hard information boundary, the host runs the broker under a dedicated
account or container, makes the storage root accessible only there, and does not
give the narrator shell, debugger or filesystem tools in that security context.

## Framing

The protocol is one UTF-8 JSON object per line. Requests are limited to 65,536
bytes. Duplicate object keys, non-finite numbers, unsupported fields and unknown
operations are rejected. Standard output is reserved for protocol responses.

Every request contains:

```json
{"v":1,"request_id":"client-unique-id","op":"capabilities","params":{}}
```

Every response echoes `request_id` when the request envelope was valid:

```json
{"v":1,"request_id":"client-unique-id","ok":true,"result":{}}
```

Errors have a stable code, a public message and an explicit indication that no
action was committed:

```json
{"v":1,"request_id":"client-unique-id","ok":false,"error":{"code":"invalid_request","message":"Request contains unsupported fields.","action_not_committed":true}}
```

## Operations

`capabilities` has no session and no parameters. It describes this interface and
the public platform capability report.

```json
{"v":1,"request_id":"r1","op":"capabilities","params":{}}
```

`start` has no session. Its caller-generated idempotency key makes retries return
the same initialized world rather than rerolling it. The result contains an
opaque bearer token and the initial public status.

```json
{"v":1,"request_id":"r2","op":"start","params":{"idempotency_key":"patrol-launch-7f3d"}}
```

`status`, `history` and `verify` are read-only. They require the opaque
`session_id` returned by `start` and an empty parameter object.

```json
{"v":1,"request_id":"r3","op":"history","session_id":"opaque-token-from-start","params":{}}
```

`act` requires one order object. Restricted-interface orders must include the
current public `expected_turn` as well as a unique order `id`. Retrying exactly
the same order is idempotent. Reusing its ID for different content, or sending a
new order for a stale turn, is rejected before state mutation.

```json
{"v":1,"request_id":"r4","op":"act","session_id":"opaque-token-from-start","params":{"order":{"id":"order-001","expected_turn":0,"activity":"listen","minutes":10,"interrupt_on":["new_contact","equipment"]}}}
```

`debrief` requires a session and no parameters. It is rejected while the
exercise is active. Once the engine marks the exercise ended, it deliberately
returns spoilers including private truth and the seed. A host that does not want
the narrator to receive after-action truth should withhold this operation.

## Persistence and failure behavior

The storage root and session directories use owner-only permissions on POSIX.
A private service key derives stable, unguessable session identifiers from
idempotency keys. The key and private saves use owner-only file permissions.
There is no operation that enumerates existing identifiers.

Every existing session is replay-verified before an operation uses it. Actions
are validated before mutation and the canonical private save is replaced
atomically. Corrupt or unverifiable saves produce a generic error without a
traceback, private value or path. The broker does not log request bodies,
responses, orders, session identifiers or debriefs.

The original path-based CLI remains available for a trusted human operator and
for replay without an LLM dependency. It is not the restricted narrator
boundary.
