export const time = (ms: number) => {
  const s = ms / 1000;
  return `${Math.floor(s / 3600)
    .toString()
    .padStart(
      2,
      "0",
    )}:${Math.floor(s / 60) % 60 < 10 ? "0" : ""}${Math.floor(s / 60) % 60}:${(s % 60).toFixed(3).padStart(6, "0")}`;
};
