import pkg from "../../package.json" with { type: "json" }

/** The release version, embedded into the compiled binary at build time. */
export const VERSION: string = pkg.version
