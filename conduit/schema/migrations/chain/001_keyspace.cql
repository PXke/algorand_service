-- Review replication strategy before production (NetworkTopologyStrategy / RF).
CREATE KEYSPACE IF NOT EXISTS algorand_platform
  WITH replication = {'class': 'NetworkTopologyStrategy', 'datacenter1': 3};
