alsa_monitor.rules = alsa_monitor.rules or {}

-- Prefer ACP routing and disable mmap probes that fail on this card.
table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "device.name", "equals", "alsa_card.platform-c051000.sound-card" },
    },
  },
  apply_properties = {
    ["api.alsa.use-acp"] = true,
    ["api.alsa.use-ucm"] = true,
    ["api.acp.auto-profile"] = true,
    ["api.acp.auto-port"] = true,
    ["api.alsa.disable-mmap"] = true,
  },
})

-- Keep the speaker node active and force S16 format (S24 is much quieter here).
table.insert(alsa_monitor.rules, {
  matches = {
    { { "node.name", "matches", "alsa_output.platform-c051000.sound-card.*" } },
    { { "node.name", "equals", "alsa_output.platform-c051000.sound-card.playback.0.0" } },
  },
  apply_properties = {
    ["node.pause-on-idle"] = false,
    ["session.suspend-timeout-seconds"] = 0,
    ["audio.format"] = "S16LE",
    ["audio.rate"] = 48000,
    ["audio.channels"] = 2,
  },
})

-- Microphone: keep PipeWire off the capture PCM. Opening it through PipeWire times
-- out in the DSP (q6asm CMD 10bcd) and can wedge speaker playback until reboot;
-- plain ALSA capture on hw:0,1 works, so voice apps read the mic directly.
table.insert(alsa_monitor.rules, {
  matches = {
    { { "node.name", "matches", "alsa_input.platform-c051000.sound-card.*" } },
  },
  apply_properties = {
    ["node.disabled"] = true,
  },
})
