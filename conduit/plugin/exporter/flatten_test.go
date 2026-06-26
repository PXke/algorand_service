package exporter

import (
	"testing"

	"github.com/algorand/go-algorand-sdk/v2/crypto"
	sdk "github.com/algorand/go-algorand-sdk/v2/types"
)

func TestFlattenSignedTxnPayment(t *testing.T) {
	t.Parallel()
	account := crypto.GenerateAccount()
	addr := account.Address

	txn := sdk.Transaction{
		Type:   sdk.PaymentTx,
		Header: sdk.Header{Sender: addr, Fee: 1000, Note: []byte("hello")},
	}
	stxn := sdk.SignedTxnInBlock{
		SignedTxn: sdk.SignedTxn{Txn: txn},
	}

	txid, sender, txnType, fee, receiver, amount, note, txnJSON, err := flattenSignedTxn(stxn, 7, 0)
	if err != nil {
		t.Fatalf("flattenSignedTxn: %v", err)
	}
	if sender != addr.String() {
		t.Fatalf("sender mismatch: %s", sender)
	}
	if txnType != sdk.PaymentTx.String() {
		t.Fatalf("txn type: %s", txnType)
	}
	if fee != 1000 {
		t.Fatalf("fee: %d", fee)
	}
	if string(note) != "hello" {
		t.Fatalf("note: %q", note)
	}
	if txid == "" {
		t.Fatal("expected non-empty txid")
	}
	if txnJSON == "" {
		t.Fatal("expected txn json")
	}
	if receiver != "" {
		t.Fatalf("expected empty receiver for incomplete payment txn, got %s", receiver)
	}
	if amount != 0 {
		t.Fatalf("expected zero amount, got %d", amount)
	}
}

func TestFlattenSignedTxnPaymentWithReceiver(t *testing.T) {
	t.Parallel()
	account := crypto.GenerateAccount()
	addr := account.Address
	recv := crypto.GenerateAccount().Address

	txn := sdk.Transaction{
		Type:     sdk.PaymentTx,
		Header:   sdk.Header{Sender: addr, Fee: 1000},
		Receiver: recv,
		Amount:   10_000,
	}
	stxn := sdk.SignedTxnInBlock{
		SignedTxn: sdk.SignedTxn{Txn: txn},
	}

	_, _, _, _, receiver, amount, _, _, err := flattenSignedTxn(stxn, 1, 0)
	if err != nil {
		t.Fatalf("flattenSignedTxn: %v", err)
	}
	if receiver != recv.String() {
		t.Fatalf("receiver: %s", receiver)
	}
	if amount != 10_000 {
		t.Fatalf("amount: %d", amount)
	}
}
