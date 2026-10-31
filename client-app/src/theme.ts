// The app's own look: OpenVue's Aura preset with a sky-blue accent and slate (blue-grey) dark
// surfaces. The structure follows Videoloft's Settings tab; the colours are this project's own.
import { definePreset } from "@openvue/themes";
import Aura from "@openvue/themes/aura";

const scale = (palette: string) => Object.fromEntries(
  [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map(n => [n, `{${palette}.${n}}`]));

export const preset = definePreset(Aura, {
  semantic: {
    primary: scale("sky"),
    colorScheme: {
      dark: { surface: { 0: "#ffffff", ...scale("slate") } },
    },
  },
  components: {
    // Aura's dark switch track is surface-700, the colour of the setting cards: an "off" switch
    // vanished into its card. A dark track with a light knob reads as off on any card.
    toggleswitch: {
      colorScheme: {
        dark: {
          root: { background: "{surface.950}", hoverBackground: "{surface.900}", disabledBackground: "{surface.800}" },
          handle: { background: "{surface.200}", hoverBackground: "{surface.0}", disabledBackground: "{surface.600}" },
        },
      },
    },
  },
});
