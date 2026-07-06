// Type-only stub. The edge-console does not use the native streaming addon, so this is
// never require()'d at runtime (getAddon() is only called on first streaming use). It exists
// solely so `tsc` can resolve the type-only import in core/libs/ts/src/streaming/*.ts
// during the site (console) image build.
module.exports = {};
