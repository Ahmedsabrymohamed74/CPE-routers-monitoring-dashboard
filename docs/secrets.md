# Secret Management

Kubernetes `Secret` objects are not enough by themselves. They are base64 encoded and visible to anyone with sufficient Kubernetes permissions.

This project supports two plain-Kubernetes patterns.

## Existing Kubernetes Secret

Create the secret outside Git and keep credentials out of the repo:

```bash
kubectl -n backend create secret generic router-dashboard-secret \
  --from-literal=HUAWEI_ROUTER_USERNAME=admin \
  --from-literal=HUAWEI_ROUTER_PASSWORD='<huawei-router-password>' \
  --from-literal=ZTE_ROUTER_USERNAME=admin \
  --from-literal=ZTE_ROUTER_PASSWORD='<zte-router-password>' \
  --from-literal=DB_USER=router_user \
  --from-literal=DB_PASSWORD='<db-password>'
```

`ZTE_SESSION_COOKIE` is optional and should normally be omitted. Use it only as a temporary debug override when you already have a browser-authenticated `zsidn` or `stok` value.

The deployment references it directly:

```yaml
envFrom:
  - secretRef:
      name: router-dashboard-secret
```

## Local stringData Secret

Use the checked-in dummy example as the starting point for a real local Secret.
Do not commit the real file.

```bash
cp secrets/secret.example.yaml secrets/router-dashboard-secret.yaml
kubectl apply -f secrets/router-dashboard-secret.yaml
```

## SOPS/Age Encrypted Secret

Use SOPS with Age when you want encrypted secret manifests in Git.

1. Generate an Age key:

```bash
age-keygen -o age.key
```

2. Put the public key in `.sops.yaml`.

3. Copy the example:

```bash
cp secrets/secret.example.yaml secrets/router-dashboard-secret.sops.yaml
```

4. Edit values, then encrypt:

```bash
sops --encrypt --in-place secrets/router-dashboard-secret.sops.yaml
```

5. Decrypt/apply during deployment:

```bash
sops --decrypt secrets/router-dashboard-secret.sops.yaml | kubectl apply -f -
```

Do not commit `age.key`.

## GitLab CI Secret Handling

Store the Age private key in a masked/protected GitLab CI variable:

```text
SOPS_AGE_KEY
```

The manual `deploy-lab` job decrypts and applies `secrets/router-dashboard-secret.sops.yaml` only when that file exists.
