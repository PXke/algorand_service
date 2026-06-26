.PHONY: conduit test fmt

LDFLAGS += -X github.com/algorand/conduit/version.Hash=dev
LDFLAGS += -X github.com/algorand/conduit/version.ShortHash=dev
LDFLAGS += -X github.com/algorand/conduit/version.CompileTime=$(shell date -u +%Y-%m-%dT%H:%M:%SZ)
LDFLAGS += -X "github.com/algorand/conduit/version.ReleaseVersion=algorand-platform-cassandra"

conduit:
	go build -ldflags='$(LDFLAGS)' -o bin/conduit ./cmd/conduit

test:
	go test -v ./...

fmt:
	go fmt ./...
