
export interface Zone {
  id: string;
  name: string;
}

export interface Tag {
  Key: string;
  Value: string;
}

export interface CallerIdentity {
  accountId: string;
  arn: string;
}

export interface IdentityDescription {
  text: string;
  isRoot: boolean;
}
