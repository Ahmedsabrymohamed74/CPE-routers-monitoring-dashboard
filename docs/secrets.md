# Secret Management

Kubernetes `Secret` objects are not enough by themselves. They are base64 encoded and visible to anyone with sufficient Kubernetes permissions.

This project supports two plain-Kubernetes patterns.

## Existing Kubernetes Secret

Create the secret outside Git and keep credentials out of the repo:

```bash
kubectl -n backend create secret generic router-dashboard-secret \
  --from-literal=ROUTER_USERNAME=admin \
  --from-literal=ROUTER_PASSWORD='<router-password>' \
  --from-literal=DB_USER=router_user \
  --from-literal=DB_PASSWORD='<db-password>'
```

The deployment references it directly:

```yaml
envFrom:
  - secretRef:
      name: router-dashboard-secret
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
cp secrets/router-dashboard-secret.sops.yaml.example secrets/router-dashboard-secret.sops.yaml
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
