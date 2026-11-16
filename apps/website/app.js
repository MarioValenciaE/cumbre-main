(() => {
  "use strict";
  const config = window.CUMBRE_CONFIG || { analytics: {} };
  const analytics = config.analytics || {};
  const form = document.querySelector("#diagnostic-form");
  const steps = [...document.querySelectorAll(".form-step")];
  const progress = document.querySelector("[data-progress]");
  const progressLabel = document.querySelector("[data-progress-label]");
  const formError = document.querySelector("[data-form-error]");
  let currentStep = 1;
  document.documentElement.classList.remove("no-js");
  document.querySelector("[data-year]").textContent = new Date().getFullYear();

  const track = (eventName, parameters = {}) => {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ event: eventName, ...parameters });
    if (typeof window.gtag === "function") window.gtag("event", eventName, parameters);
    if (typeof window.fbq === "function") window.fbq("trackCustom", eventName, parameters);
  };
  const loadScript = (src) => new Promise((resolve) => { const script = document.createElement("script"); script.src = src; script.async = true; script.onload = resolve; document.head.append(script); });
  const startAnalytics = async () => {
    if (analytics.gaMeasurementId) { await loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(analytics.gaMeasurementId)}`); window.dataLayer = window.dataLayer || []; window.gtag = function gtag(){ window.dataLayer.push(arguments); }; window.gtag("js", new Date()); window.gtag("config", analytics.gaMeasurementId, { anonymize_ip: true }); }
    if (analytics.clarityProjectId) { window.clarity = window.clarity || function clarity(){ (window.clarity.q = window.clarity.q || []).push(arguments); }; const script = document.createElement("script"); script.async = true; script.src = `https://www.clarity.ms/tag/${encodeURIComponent(analytics.clarityProjectId)}`; document.head.append(script); }
    if (analytics.metaPixelId) { window.fbq = window.fbq || function fbq(){ (window.fbq.callMethod ? window.fbq.callMethod : window.fbq.queue.push).apply(window.fbq, arguments); }; window.fbq.push = window.fbq; window.fbq.loaded = true; window.fbq.version = "2.0"; window.fbq.queue = []; await loadScript("https://connect.facebook.net/en_US/fbevents.js"); window.fbq("init", analytics.metaPixelId); window.fbq("track", "PageView"); }
  };
  const banner = document.querySelector("[data-cookie-banner]");
  const setConsent = (choice) => { localStorage.setItem("cumbre-cookie-consent", choice); banner.hidden = true; if (choice === "accepted") startAnalytics(); track("cookie_consent", { choice }); };
  const storedConsent = localStorage.getItem("cumbre-cookie-consent");
  if (storedConsent === "accepted") startAnalytics(); else if (!storedConsent) banner.hidden = false;
  document.querySelector("[data-cookie-accept]").addEventListener("click", () => setConsent("accepted"));
  document.querySelector("[data-cookie-reject]").addEventListener("click", () => setConsent("necessary"));
  document.querySelector("[data-cookie-settings]").addEventListener("click", () => { banner.hidden = false; });

  const navToggle = document.querySelector(".nav-toggle"); const nav = document.querySelector(".main-nav");
  navToggle.addEventListener("click", () => { const open = nav.classList.toggle("is-open"); navToggle.setAttribute("aria-expanded", String(open)); });
  nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => { nav.classList.remove("is-open"); navToggle.setAttribute("aria-expanded", "false"); }));
  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add("is-visible"); observer.unobserve(entry.target); } }), { threshold: 0.12 });
  document.querySelectorAll("[data-reveal]").forEach((item) => observer.observe(item));
  const scrollIndicator = document.querySelector(".scroll-indicator span");
  window.addEventListener("scroll", () => { const total = document.documentElement.scrollHeight - window.innerHeight; scrollIndicator.style.width = `${total ? (window.scrollY / total) * 100 : 0}%`; }, { passive: true });
  const showStep = (step) => { currentStep = step; steps.forEach((item) => item.classList.toggle("is-active", item.dataset.step === String(step))); if (step <= 5) { progress.style.width = `${step * 20}%`; progressLabel.textContent = `${String(step).padStart(2, "0")} / 05`; } form.querySelector(".form-step.is-active input")?.focus(); };
  const answerPresent = (step) => Boolean(form.querySelector(`.form-step[data-step="${step}"] input:checked`));
  form.querySelectorAll(".form-next").forEach((button) => button.addEventListener("click", () => { if (!answerPresent(currentStep)) { formError.textContent = "Elige una opción para continuar."; return; } formError.textContent = ""; track("diagnostic_step_completed", { step: currentStep }); showStep(currentStep + 1); if (currentStep === 5) buildResult(); }));
  form.querySelectorAll(".form-back").forEach((button) => button.addEventListener("click", () => { formError.textContent = ""; showStep(Math.max(1, currentStep - 1)); }));
  form.querySelectorAll("input[type=radio]").forEach((input) => input.addEventListener("change", () => { formError.textContent = ""; }));
  const getValue = (name) => form.querySelector(`input[name="${name}"]:checked`)?.value || "";
  const buildResult = () => {
    const results = {
      datos: ["crear visibilidad para decidir", "Antes de pensar en un tablero, conviene identificar las preguntas que hoy toman demasiado tiempo y las fuentes de información disponibles."],
      procesos: ["eliminar fricción del proceso", "El siguiente paso puede ser mapear el trabajo actual, distinguir lo que debe mantenerse humano y detectar tareas repetitivas que se pueden simplificar."],
      sistemas: ["conectar el flujo de información", "La conversación podría empezar por entender qué sistemas intervienen, dónde se duplica el trabajo y cuál sería el flujo de información más confiable."],
      crecimiento: ["preparar una operación que pueda escalar", "Un diagnóstico puede ayudar a ver qué partes de la operación necesitan estructura antes de que el crecimiento haga más costosos los problemas actuales."]
    };
    const [title, copy] = results[getValue("focus")] || ["entender la oportunidad", "Un diagnóstico breve puede ayudar a mapear el proceso actual, validar el impacto y decidir si vale la pena intervenir."];
    form.querySelector("[data-result-title]").textContent = title; form.querySelector("[data-result-copy]").textContent = copy; track("diagnostic_completed", { focus: getValue("focus") });
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const name = form.elements.name.value.trim(); const company = form.elements.company.value.trim(); const email = form.elements.email.value.trim(); const consent = form.elements.consent.checked;
    if (!name || !company || !email || !consent) { formError.textContent = "Completa tus datos y autoriza el contacto para continuar."; return; }
    if (!/^\S+@\S+\.\S+$/.test(email)) { formError.textContent = "Escribe un correo válido."; return; }
    const summary = `Diagnóstico inicial Cumbre\n\nNombre: ${name}\nEmpresa: ${company}\nCorreo: ${email}\nFoco: ${getValue("focus")}\nProceso actual: ${getValue("process")}\nImpacto: ${getValue("impact")}\nPrioridad: ${getValue("urgency")}`;
    const contactActions = form.querySelector("[data-contact-actions]");
    const channels = [];
    if (config.whatsappNumber) channels.push({ label: "Enviar por WhatsApp", href: `https://wa.me/${config.whatsappNumber}?text=${encodeURIComponent(summary)}`, event: "lead_whatsapp_clicked" });
    if (config.contactEmail) channels.push({ label: "Enviar por correo", href: `mailto:${encodeURIComponent(config.contactEmail)}?subject=${encodeURIComponent("Diagnóstico inicial — " + company)}&body=${encodeURIComponent(summary)}`, event: "lead_email_clicked" });
    contactActions.innerHTML = "";
    channels.forEach((channel) => { const link = document.createElement("a"); link.className = "button button-light"; link.href = channel.href; link.textContent = channel.label; link.addEventListener("click", () => track(channel.event, { focus: getValue("focus") })); contactActions.append(link); });
    contactActions.hidden = channels.length === 0;
    const message = channels.length ? "Elige cómo quieres enviarnos tu resumen para continuar la conversación." : "El diagnóstico funciona, pero antes de publicar hay que configurar WhatsApp o correo en site.config.js para recibir los leads.";
    track("lead_submitted", { focus: getValue("focus"), channels: channels.length || "not_configured" }); form.querySelector("[data-submit-message]").textContent = message; steps.forEach((item) => item.classList.toggle("is-active", item.dataset.step === "submitted")); progress.style.width = "100%"; progressLabel.textContent = "LISTO";
  });
  document.querySelectorAll("a[href='#diagnostico']").forEach((link) => link.addEventListener("click", () => track("cta_diagnostic_clicked")));
})();
