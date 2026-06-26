package exporter

import (
	"testing"

	"github.com/gocql/gocql"
)

func TestParseConsistency(t *testing.T) {
	t.Parallel()
	cases := map[string]gocql.Consistency{
		"ONE":          gocql.One,
		"local_one":    gocql.LocalOne,
		"QUORUM":       gocql.Quorum,
		"":             gocql.Quorum,
		"unknown":      gocql.Quorum,
		"EACH_QUORUM":  gocql.EachQuorum,
	}
	for input, want := range cases {
		if got := parseConsistency(input); got != want {
			t.Fatalf("parseConsistency(%q) = %v, want %v", input, got, want)
		}
	}
}

func TestSplitStatements(t *testing.T) {
	t.Parallel()
	stmts := splitStatements(`
CREATE TABLE a (id int PRIMARY KEY);

CREATE TABLE b (id int PRIMARY KEY)
`)
	if len(stmts) != 2 {
		t.Fatalf("expected 2 statements, got %d", len(stmts))
	}
	if stmts[0][:12] != "CREATE TABLE" {
		t.Fatalf("unexpected first statement: %q", stmts[0])
	}
}
