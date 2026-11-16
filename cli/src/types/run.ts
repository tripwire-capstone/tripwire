export interface RunOptions {
  cwd?: string;
  // false for output with secrets
  logStdout?: boolean;
  // Exit codes besides 0 that aren't failures
  okExitCodes?: number[];
  // Called with each stdout line as it arrives; what it returns is logged (and returned) instead
  // of the line, undefined drops it
  onLine?: (line: string) => string | undefined;
}
