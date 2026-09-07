<script lang="ts">
  /** List an endpoint (x402.pxke.me/list) -- thin page wrapper around the
   * existing X402RegisterForm, which already has a real wallet-pay flow
   * (lib/x402/pay.ts's payWithWallet); this redesign only moves it out from
   * behind a tab labelled "Register" (the audit's N7: "Register" meant two
   * different things on this site) and onto its own top-level page named
   * for what it does. */
  import { messages, t } from '../../lib/i18n'
  import PageMeta from '../../components/PageMeta.svelte'
  import X402RegisterForm from '../../components/x402/X402RegisterForm.svelte'
</script>

<PageMeta
  title={t($messages, 'x402RegisterTitle')}
  description={t($messages, 'x402RegisterLead')}
  path="/list"
/>

<div class="page stack x402">
  <X402RegisterForm />
</div>
