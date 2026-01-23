document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("id_psk");
  if (!input) {
    return;
  }

  const button = document.createElement("button");
  button.type = "button";
  button.className = "button";
  button.textContent = "Generate PSK";
  button.style.marginLeft = "8px";

  button.addEventListener("click", () => {
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let value = "";
    for (let i = 0; i < 12; i += 1) {
      value += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  input.insertAdjacentElement("afterend", button);
});
