import type LuteConnect from '@galaxypay/lute-connect'
import { getAlgodClient, bytesToBase64 } from './arc0025'

const SITE_NAME = 'PXke Algorand'

export type Arc0060Proof = {
  proofMethod: 'arc0060'
  arc0060: {
    data_b64: string
    signature_b64: string
    authenticator_data_b64: string
    domain: string
  }
}

let client: LuteConnect | null = null
let loading: Promise<typeof import('@galaxypay/lute-connect')> | null = null

async function loadLuteModule() {
  if (!loading) loading = import('@galaxypay/lute-connect')
  return loading
}

async function getLute(): Promise<LuteConnect> {
  if (!client) {
    const { default: LuteConnectCtor } = await loadLuteModule()
    client = new LuteConnectCtor(SITE_NAME)
  }
  return client
}

/**
 * Lute's `/genesis` algod call returns the raw genesis JSON as *text*
 * (algosdk's Genesis request is typed `JSONRequest<string>` and its
 * `prepare()` returns `response.getJSONText()` verbatim) — it is NOT
 * pre-parsed into an object. Lute's own README example treats it as
 * already-parsed (`genesis.network`), which would silently produce
 * "undefined-undefined". Parse it ourselves.
 */
async function genesisId(): Promise<string> {
  const algod = await getAlgodClient()
  const text = await algod.genesis().do()
  const parsed = JSON.parse(text) as { network?: string; id?: string }
  if (!parsed.network || !parsed.id) throw new Error('Could not resolve network genesis ID')
  return `${parsed.network}-${parsed.id}`
}

export async function luteConnect(): Promise<string> {
  const lute = await getLute()
  const gid = await genesisId()
  const accounts = await lute.connect(gid)
  const addr = accounts[0]
  if (!addr) throw new Error('No account returned from wallet')
  return addr
}

async function sha256(bytes: Uint8Array): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer)
  return new Uint8Array(digest)
}

/**
 * Lute implements ARC-0060 `signData` natively (StdSignData/StdSignMetadata
 * exactly matching the backend's Arc0060Proof shape — see
 * backend/app/modules/auth/utils/arc0060_verify.py). The signed payload is
 * the CAIP-122 challenge object the backend issued verbatim: the backend
 * re-canonicalizes it server-side (RFC 8785 sorted-key JSON) before
 * checking the signature, so client-side key order doesn't matter, only
 * that the JSON round-trips with the same field values.
 */
export async function luteSignLoginProof(
  walletAddress: string,
  challenge: { caip122: Record<string, unknown> },
): Promise<Arc0060Proof> {
  const lute = await getLute()
  const algosdk = (await import('algosdk')).default
  const { ScopeType } = await loadLuteModule()

  const domain = String(challenge.caip122.domain ?? '')
  if (!domain) throw new Error('Invalid auth challenge (missing domain)')

  const dataBytes = new TextEncoder().encode(JSON.stringify(challenge.caip122))
  const dataB64 = bytesToBase64(dataBytes)
  const authenticatorData = await sha256(new TextEncoder().encode(domain))
  const signer = algosdk.decodeAddress(walletAddress).publicKey

  const response = await lute.signData(
    { data: dataB64, signer, domain, authenticatorData },
    { scope: ScopeType.AUTH, encoding: 'base64' },
  )

  return {
    proofMethod: 'arc0060',
    arc0060: {
      data_b64: dataB64,
      signature_b64: bytesToBase64(response.signature),
      authenticator_data_b64: bytesToBase64(authenticatorData),
      domain,
    },
  }
}

/** Lute is popup-based (no persistent WalletConnect session) — nothing to tear down. */
export async function luteDisconnect(): Promise<void> {
  client = null
}
