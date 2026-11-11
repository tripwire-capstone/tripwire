
export interface DbSnapshot {
  id: string;
  created: string;
}

export interface VolumeSnapshot {
  id: string;
  created: string;
  tags: { Key: string; Value: string }[];
}

export interface Snapshots {
  db: DbSnapshot[];
  volumes: VolumeSnapshot[];
}

export interface SnapshotSet {
  db: DbSnapshot;
  volume: VolumeSnapshot;
}

export interface Rotation {
  keep: SnapshotSet;
  deleteDb: string[];
  deleteVolumes: string[];
}
