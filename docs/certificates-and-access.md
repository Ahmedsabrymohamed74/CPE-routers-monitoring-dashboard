# Certificates And Access

## Current TLS Pattern

The ingress references an existing TLS secret:

```yaml
tls:
  - hosts:
      - router-dashboard.lab.local
    secretName: router-dashboard-tls
```

Create or rotate it with:

```bash
kubectl -n backend create secret tls router-dashboard-tls \
  --cert=router-dashboard.crt \
  --key=router-dashboard.key \
  --dry-run=client -o yaml | kubectl apply -f -
```

Do not commit certificate private keys.

## Better Long-Term TLS

Use cert-manager if the dashboard will have a real DNS name and ACME-compatible issuer. Without Helm, this means adding cert-manager annotations to `k8s_mani/dashboard_ingress.yml`.

For private lab domains, use a private CA and install the CA certificate on phones/laptops.

## Smartphone/LAN Access

Current shape:

- `router-dashboard` Service: `ClusterIP`
- Traefik Service: NodePort `80:30080`, `443:30443`
- Ingress host: `router-dashboard.lab.local`

Access options:

- `http://<node-ip>:30080` with the correct host routing if Traefik is configured for it.
- `https://router-dashboard.lab.local` when LAN DNS resolves the host to the ingress/node address.

For hostname access from phones, configure LAN DNS so `router-dashboard.lab.local` resolves to the Kubernetes node or ingress address.

## Outside Access

Preferred:

- VPN to the lab network.
- Authenticated tunnel.
- Reverse proxy with TLS and authentication.

Avoid exposing the dashboard directly to the public internet.
