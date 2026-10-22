import { apply } from "./lib/index.js";

const disposers = [];
const ctx = {
  effect(fn) {
    const dispose = fn();
    if (typeof dispose === "function") disposers.push(dispose);
  },
};

apply(ctx);
console.log("HOST APPLY OK, pid", process.pid);

setTimeout(() => {
  console.log("DISPOSING");
  for (const dispose of disposers) {
    try {
      dispose();
    } catch (error) {
      console.error(error);
    }
  }
}, 6000);
