/**
 * ARC-0025 login-proof helpers shared by wallets that don't implement
 * ARC-0060 `signData` (e.g. Defly). The proof is a signed 0-ALGO
 * self-payment transaction whose note carries the signing message —
 * nothing is broadcast, it only proves key ownership.
 *
 * Extracted from pera.ts's inline implementation (see arc0025.test.ts,
 * which pins the built transaction's shape) so Defly can share it
 * without duplicating the algod-client/txn-building logic.
 */
import { config } from '../config'

export async function getAlgodClient() {
  const algosdk = (await import('algosdk')).default
  const server =
    config.algodApiUrl.startsWith('http://') || config.algodApiUrl.startsWith('https://')
      ? config.algodApiUrl
      : `${window.location.origin}${config.algodApiUrl}`
  return new algosdk.Algodv2('', server, '')
}

export async function buildAuthPaymentTxn(walletAddress: string, signingMessage: string) {
  const algosdk = (await import('algosdk')).default
  const algod = await getAlgodClient()
  const suggested = await algod.getTransactionParams().do()
  return algosdk.makePaymentTxnWithSuggestedParamsFromObject({
    sender: walletAddress,
    receiver: walletAddress,
    amount: 0,
    note: new TextEncoder().encode(signingMessage),
    suggestedParams: {
      ...suggested,
      fee: 0,
      flatFee: true,
    },
  })
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}
