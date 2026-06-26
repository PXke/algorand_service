package exporter

import (
	"strings"

	"github.com/gocql/gocql"
	sdk "github.com/algorand/go-algorand-sdk/v2/types"
)

func parseConsistency(name string) gocql.Consistency {
	switch strings.ToUpper(strings.TrimSpace(name)) {
	case "ONE":
		return gocql.One
	case "TWO":
		return gocql.Two
	case "THREE":
		return gocql.Three
	case "LOCAL_QUORUM":
		return gocql.LocalQuorum
	case "EACH_QUORUM":
		return gocql.EachQuorum
	case "LOCAL_ONE":
		return gocql.LocalOne
	default:
		return gocql.Quorum
	}
}

func paymentReceiverAmount(txn sdk.Transaction) (receiver string, amount int64, ok bool) {
	if txn.Type != sdk.PaymentTx {
		return "", 0, false
	}
	return txn.Receiver.String(), int64(txn.Amount), true
}

func splitStatements(cql string) []string {
	parts := strings.Split(cql, ";")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		stmt := strings.TrimSpace(p)
		if stmt != "" {
			out = append(out, stmt)
		}
	}
	return out
}
