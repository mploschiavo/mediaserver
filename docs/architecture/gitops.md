# GitOps Workflow

This stack is GitOps-friendly even if you operate it with imperative scripts today.

## Principles

- Git is the change ledger.
- Desired state is declared in manifests, profile YAML, and per-service YAML.
- Runtime state is reconciled back to declared state.

## Suggested Flow

1. Branch from main.
2. Update declarative config (`deploy/k8s/`, `deploy/compose/`, `contracts/`, `src/` as needed).
3. Run local tests (cross-platform; see [First-time setup](../how-to/deployment.md#first-time-setup)):
```bash
media-stack-run-unit-tests
# Linux convenience: bash bin/test/test.sh
```
4. Deploy to isolated namespace:
```bash
media-stack-install --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
# Linux convenience: bash bin/install/install.sh ...
```
5. Verify integration and routes:
```bash
.venv/bin/python -m media_stack.cli.commands.verify_flow_main media-stack-dev
# (verify_flow_main has no console-script entry yet)
media-stack-microk8s-smoke <NODE_IP> media-stack-dev
```
6. Promote same commit to primary namespace.

## Promotion Safety

- Keep namespace-specific differences in arguments/env (namespace, ingress domain, host root).
- Avoid editing manifests manually between environments.
- Use the same profile YAML and per-service YAML unless there is an explicit environment override.

## Drift Handling

If production diverges from Git-defined state:
- rerun contracts/reconcile scripts
- capture the drift cause
- either promote intended change to Git or remove runtime-only change

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
