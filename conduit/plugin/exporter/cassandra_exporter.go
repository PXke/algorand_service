package exporter

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/gocql/gocql"
	"github.com/sirupsen/logrus"
	"gopkg.in/yaml.v3"

	sdk "github.com/algorand/go-algorand-sdk/v2/types"

	"github.com/algorand/conduit/conduit/data"
	"github.com/algorand/conduit/conduit/plugins"
	"github.com/algorand/conduit/conduit/plugins/exporters"
)

const pluginName = "cassandra"

//go:embed sample.yaml
var sampleConfig string

var metadata = plugins.Metadata{
	Name:         pluginName,
	Description:  "Export algod follower blocks and transactions into Apache Cassandra.",
	Deprecated:   false,
	SampleConfig: sampleConfig,
}

var errMissingDelta = errors.New("ledger state delta missing; use algod importer in follower mode")

func init() {
	exporters.Register(pluginName, exporters.ExporterConstructorFunc(func() exporters.Exporter {
		return &cassandraExporter{}
	}))
}

type cassandraExporter struct {
	log     *logrus.Logger
	cfg     Config
	session *gocql.Session
}

func (e *cassandraExporter) Metadata() plugins.Metadata {
	return metadata
}

func (e *cassandraExporter) Config() string {
	out, _ := yaml.Marshal(e.cfg)
	return string(out)
}

func (e *cassandraExporter) RoundRequest(cfg plugins.PluginConfig) (uint64, error) {
	var c Config
	if err := cfg.UnmarshalConfig(&c); err != nil {
		return 0, nil
	}
	session, err := openSession(c)
	if err != nil {
		return 0, nil
	}
	defer session.Close()

	// conduit_meta.value is a text column (see schema + upsertMeta, which writes
	// fmt.Sprintf("%d", ...)). Scan into a string and parse, not directly into
	// int64 — gocql cannot unmarshal text into *int64 and would error on every
	// restart once next_round exists, stalling ingestion.
	var nextRoundStr string
	err = session.Query(
		`SELECT value FROM conduit_meta WHERE id = ?`,
		"next_round",
	).Consistency(parseConsistency(c.Consistency)).Scan(&nextRoundStr)
	if err != nil {
		if errors.Is(err, gocql.ErrNotFound) {
			return 0, nil
		}
		return 0, fmt.Errorf("read next_round: %w", err)
	}
	nextRound, err := strconv.ParseInt(nextRoundStr, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse next_round %q: %w", nextRoundStr, err)
	}
	if nextRound < 0 {
		return 0, nil
	}
	return uint64(nextRound), nil
}

func (e *cassandraExporter) Init(
	ctx context.Context,
	initProvider data.InitProvider,
	cfg plugins.PluginConfig,
	logger *logrus.Logger,
) error {
	_ = ctx
	e.log = logger
	if err := cfg.UnmarshalConfig(&e.cfg); err != nil {
		return fmt.Errorf("unmarshal config: %w", err)
	}
	if len(e.cfg.Hosts) == 0 {
		return errors.New("hosts must not be empty")
	}
	if e.cfg.Port == 0 {
		e.cfg.Port = 9042
	}
	if e.cfg.Keyspace == "" {
		e.cfg.Keyspace = "algorand_platform"
	}
	if e.cfg.Consistency == "" {
		e.cfg.Consistency = "QUORUM"
	}

	session, err := openSession(e.cfg)
	if err != nil {
		return err
	}
	e.session = session

	if e.cfg.AutoMigrate {
		for _, stmt := range splitStatements(schemaCQL) {
			if err := session.Query(stmt).Exec(); err != nil {
				return fmt.Errorf("schema migration failed: %w", err)
			}
		}
	}

	genesis := initProvider.GetGenesis()
	if genesis != nil {
		raw, err := json.Marshal(genesis)
		if err != nil {
			return fmt.Errorf("encode genesis: %w", err)
		}
		if err := e.upsertMeta("genesis_json", string(raw)); err != nil {
			return err
		}
	}

	nextDB := uint64(initProvider.NextDBRound())
	if err := e.upsertMeta("next_round", fmt.Sprintf("%d", nextDB)); err != nil {
		return err
	}

	e.log.Infof(
		"cassandra exporter ready keyspace=%s next_round=%d auto_migrate=%v",
		e.cfg.Keyspace,
		nextDB,
		e.cfg.AutoMigrate,
	)
	return nil
}

func (e *cassandraExporter) Close() error {
	if e.session != nil {
		e.session.Close()
	}
	return nil
}

func (e *cassandraExporter) Receive(exportData data.BlockData) error {
	if exportData.Delta == nil && exportData.Round() != 0 {
		return errMissingDelta
	}

	round := exportData.Round()
	headerJSON, err := json.Marshal(exportData.BlockHeader)
	if err != nil {
		return fmt.Errorf("marshal block header: %w", err)
	}

	consistency := parseConsistency(e.cfg.Consistency)
	now := time.Now().UTC()

	if err := e.session.Query(
		`INSERT INTO blocks (round, block_timestamp, proposer, txn_count, block_header_json, ingested_at)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		int64(round),
		int64(exportData.BlockHeader.TimeStamp),
		exportData.BlockHeader.Proposer.String(),
		len(exportData.Payset),
		string(headerJSON),
		now,
	).Consistency(consistency).Exec(); err != nil {
		return fmt.Errorf("insert block %d: %w", round, err)
	}

	batch := e.session.NewBatch(gocql.LoggedBatch).WithContext(context.Background())

	for intra, stxn := range exportData.Payset {
		txid, sender, txnType, fee, receiver, amount, note, txnJSON, err := flattenSignedTxn(stxn, round, intra)
		if err != nil {
			e.log.Warnf("round %d intra %d: skip txn: %v", round, intra, err)
			continue
		}

		batch.Query(
			`INSERT INTO transactions_by_round (round, intra, txid, sender, txn_type, fee, note, txn_json, receiver, amount_microalgos)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			int64(round), intra, txid, sender, txnType, int64(fee), note, txnJSON, receiver, amount,
		)
		batch.Query(
			`INSERT INTO transactions_by_id (txid, round, intra, sender, txn_type, txn_json, receiver, amount_microalgos)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
			txid, int64(round), intra, sender, txnType, txnJSON, receiver, amount,
		)
		if e.cfg.WriteTransactionsBySender && sender != "" {
			batch.Query(
				`INSERT INTO transactions_by_sender (sender, round, txid, intra, txn_type, receiver, amount_microalgos)
				 VALUES (?, ?, ?, ?, ?, ?, ?)`,
				sender, int64(round), txid, intra, txnType, receiver, amount,
			)
		}
		if e.cfg.WriteTransactionsByReceiver && receiver != "" {
			batch.Query(
				`INSERT INTO transactions_by_receiver (receiver, round, txid, intra, sender, txn_type, amount_microalgos)
				 VALUES (?, ?, ?, ?, ?, ?, ?)`,
				receiver, int64(round), txid, intra, sender, txnType, amount,
			)
		}
	}

	if batch.Size() > 0 {
		if err := e.session.ExecuteBatch(batch); err != nil {
			return fmt.Errorf("insert transactions round %d: %w", round, err)
		}
	}

	if err := e.upsertMeta("next_round", fmt.Sprintf("%d", round+1)); err != nil {
		return err
	}
	if err := e.upsertMeta("last_ingested_round", fmt.Sprintf("%d", round)); err != nil {
		return err
	}

	return nil
}

func (e *cassandraExporter) upsertMeta(id, value string) error {
	return e.session.Query(
		`INSERT INTO conduit_meta (id, value, updated_at) VALUES (?, ?, ?)`,
		id, value, time.Now().UTC(),
	).Consistency(parseConsistency(e.cfg.Consistency)).Exec()
}

func openSession(cfg Config) (*gocql.Session, error) {
	cluster := gocql.NewCluster(cfg.Hosts...)
	cluster.Port = cfg.Port
	cluster.Keyspace = cfg.Keyspace
	cluster.Consistency = parseConsistency(cfg.Consistency)
	cluster.Timeout = 15 * time.Second
	cluster.ConnectTimeout = 10 * time.Second
	if cfg.Username != "" {
		cluster.Authenticator = gocql.PasswordAuthenticator{
			Username: cfg.Username,
			Password: cfg.Password,
		}
	}
	return cluster.CreateSession()
}

func flattenSignedTxn(
	stxn sdk.SignedTxnInBlock,
	round uint64,
	intra int,
) (
	txid, sender, txnType string,
	fee uint64,
	receiver string,
	amount int64,
	note []byte,
	txnJSON string,
	err error,
) {
	txn := stxn.SignedTxn.Txn
	sender = txn.Sender.String()
	txnType = txn.Type.String()
	fee = txn.Fee
	if recv, amt, ok := paymentReceiverAmount(txn); ok {
		receiver = recv
		amount = amt
	}
	if len(txn.Note) > 0 {
		note = append([]byte(nil), txn.Note...)
	}

	raw, err := json.Marshal(stxn.SignedTxn)
	if err != nil {
		return "", "", "", 0, "", 0, nil, "", err
	}
	txnJSON = string(raw)

	txid = stxn.SignedTxn.ID().String()
	if txid == "" {
		txid = fmt.Sprintf("%d-%d", round, intra)
	}
	return txid, sender, txnType, fee, receiver, amount, note, txnJSON, nil
}
