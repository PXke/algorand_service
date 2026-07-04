{{flutter_js}}
{{flutter_build_config}}

_flutter.loader.load({
  config: {
    // Avoid SharedArrayBuffer / cross-origin isolation requirements on skwasm.
    forceSingleThreadedSkwasm: true,
  },
});
