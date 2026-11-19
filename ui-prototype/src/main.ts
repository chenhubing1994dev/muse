import { mount } from "svelte";
// Legacy theme sheets load first so the 璇玑 design system (imported by App)
// always wins the cascade. Do not reorder without checking computed styles.
import "../style.css";
import "./workflow.css";
import "./value-studio.css";
import "./cyberpunk.css";
import "./creative-studio.css";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("Muse dashboard mount point #app was not found");
}

mount(App, { target });
