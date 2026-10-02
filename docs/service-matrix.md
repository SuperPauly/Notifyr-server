# Service classification

| Service | Classification | Runtime | Network | Persistence |
|---|---|---|---|---|
| Notifyr | web-ui | FastAPI HTTPS/JSON API with OpenAPI web documentation; embedded worker | Existing external pangolin network, no host port publication | Named volume /data: SQLite identity, notifications, VAPID keys, subscriptions and delivery queue |

The API is the public front door. The SQLite database and embedded worker have
no independent exposed ports. This repository prepares its own Compose project;
it does not modify or start the parent infrastructure stack.
