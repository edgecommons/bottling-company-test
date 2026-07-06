// Type-only stub for @edgecommons/streamlog-node — the unpublished native napi streaming
// addon (the shared Rust edgestreamlog core built as a Node addon). It is an OPTIONAL dependency
// of the edgecommons TS lib, so a clean `npm install` in the console image silently skips it and
// then `tsc` fails to resolve the type-only imports in src/streaming/{native,service}.ts.
//
// The edge-console never uses streaming, so we only need enough of the type surface for `tsc`
// to compile. This mirrors exactly what the lib references (LogEvent, StreamHandle,
// StreamService.open + a couple of loosely-typed instance methods). It is NEVER loaded at
// runtime (getAddon() is only called on first actual streaming use). The edgecommons repo is
// untouched — this file lives only inside the site (console) image.

export interface LogEvent {
  level: number;
  target: string;
  message: string;
}

export declare class StreamHandle {
  [key: string]: any;
}

export declare class StreamService {
  static open(configJson: string): StreamService;
  [key: string]: any;
}

export declare function setLogCallback(cb: (err: Error | null, ev: LogEvent) => void): void;
export declare function registerSinkCallback(
  streamName: string,
  cb: (err: Error | null, arg: [number, any[]]) => void,
): void;
export declare function resolveOutcome(
  batchId: number,
  code: number,
  failedOffsets?: number[] | null,
): void;
