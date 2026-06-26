package exporter

// Config is the cassandra exporter section of conduit.yml.
type Config struct {
	Hosts                      []string `yaml:"hosts"`
	Port                       int      `yaml:"port"`
	Keyspace                   string   `yaml:"keyspace"`
	Username                   string   `yaml:"username"`
	Password                   string   `yaml:"password"`
	Consistency                string   `yaml:"consistency"`
	AutoMigrate                bool     `yaml:"auto_migrate"`
	WriteTransactionsBySender   bool `yaml:"write_transactions_by_sender"`
	WriteTransactionsByReceiver bool `yaml:"write_transactions_by_receiver"`
}
