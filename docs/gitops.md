# GitOps Workflow

This stack is GitOps-friendly even if you operate it with imperative scripts today.

## Principles

- Git is the change ledger.
- Desired state is declared in manifests and bootstrap config.
- Runtime state is reconciled back to declared state.

## Suggested Flow

1. Branch from main.
2. Update declarative config (`k8s/`, `contracts/`, `bin/` as needed).
3. Run local tests:
```bash
bash bin/test.sh
```
4. Deploy to isolated namespace:
```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
```
5. Verify integration and routes:
```bash
bash bin/verify-flow.sh media-stack-dev
bash bin/microk8s-smoke-test.sh <NODE_IP> media-stack-dev
```
6. Promote same commit to primary namespace.

## Promotion Safety

- Keep namespace-specific differences in arguments/env (namespace, ingress domain, host root).
- Avoid editing manifests manually between environments.
- Use the same profile and bootstrap config unless there is an explicit environment override.

## Drift Handling

If production diverges from Git-defined state:
- rerun contracts/reconcile scripts
- capture the drift cause
- either promote intended change to Git or remove runtime-only change

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
