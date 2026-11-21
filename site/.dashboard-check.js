
      const loginCard = document.querySelector("#login-card");
      const homeCard = document.querySelector("#home-card");
      const loginButton = document.querySelector("#login-button");
      const logoutButton = document.querySelector("#logout-button");
      const profileToggle = document.querySelector("#profile-toggle");
      const profileMenu = document.querySelector("#profile-menu");
      const profileAvatar = document.querySelector("#profile-avatar");
      const avatarFallback = document.querySelector("#avatar-fallback");
      const profileDisplayName = document.querySelector("#profile-display-name");
      const profileUsername = document.querySelector("#profile-username");
      const statusText = document.querySelector("#status");
      const verification = document.querySelector("#verification");
      const verificationCode = document.querySelector("#verification-code");
      const accountName = document.querySelector("#account-name");
      const terminalForm = document.querySelector("#terminal-form");
      const terminalInput = document.querySelector("#terminal-input");
      const terminalOutput = document.querySelector("#terminal-output");
      const dashboardTabs = [...document.querySelectorAll("[data-dashboard-tab]")];
      const consoleView = document.querySelector("#console-view");
      const logsView = document.querySelector("#logs-view");
      const databaseView = document.querySelector("#database-view");
      const sessionsView = document.querySelector("#sessions-view");
      const logsSearch = document.querySelector("#logs-search");
      const logsUsers = document.querySelector("#logs-users");
      const logsMore = document.querySelector("#logs-more");
      const logsChatName = document.querySelector("#logs-chat-name");
      const logsChatId = document.querySelector("#logs-chat-id");
      const logsMessages = document.querySelector("#logs-messages");
      const logsHistoryButton = document.querySelector("#logs-history-button");
      const historyDialog = document.querySelector("#history-dialog");
      const historyClose = document.querySelector("#history-close");
      const historyTitle = document.querySelector("#history-title");
      const historyList = document.querySelector("#history-list");
      const logsSendForm = document.querySelector("#logs-send-form");
      const logsSendInput = document.querySelector("#logs-send-input");
      const logsSendButton = document.querySelector("#logs-send-button");
      const databaseSearchForm = document.querySelector("#database-search-form");
      const databaseUserIdInput = document.querySelector("#database-user-id");
      const databaseStatus = document.querySelector("#database-status");
      const databaseResult = document.querySelector("#database-result");
      const databaseAvatar = document.querySelector("#database-avatar");
      const databaseAvatarFallback = document.querySelector("#database-avatar-fallback");
      const databaseUserName = document.querySelector("#database-user-name");
      const databaseUserIdentity = document.querySelector("#database-user-identity");
      const databaseUserStatus = document.querySelector("#database-user-status");
      const databaseBotBlockedStatus = document.querySelector("#database-bot-blocked-status");
      const databaseSettingsButton = document.querySelector("#database-settings-button");
      const userSettingsDialog = document.querySelector("#user-settings-dialog");
      const settingsTitle = document.querySelector("#settings-title");
      const settingsClose = document.querySelector("#settings-close");
      const settingsDone = document.querySelector("#settings-done");
      const settingsNavigation = [...document.querySelectorAll("[data-settings-section]")];
      const settingsSections = [...document.querySelectorAll(".settings-section")];
      const settingsHistoryList = document.querySelector("#settings-history-list");
      const settingsAdminStatus = document.querySelector("#settings-admin-status");
      const settingsAdminLevel = document.querySelector("#settings-admin-level");
      const settingsAdminLevelValue = document.querySelector("#settings-admin-level-value");
      const settingsSendBriefing = document.querySelector("#settings-send-briefing");
      const settingsChangeLevel = document.querySelector("#settings-change-level");
      const settingsRemoveAdmin = document.querySelector("#settings-remove-admin");
      const settingsAdminMessage = document.querySelector("#settings-admin-message");
      const settingsPrefixList = document.querySelector("#settings-prefix-list");
      const settingsSavePrefixes = document.querySelector("#settings-save-prefixes");
      const settingsPrefixMessage = document.querySelector("#settings-prefix-message");
      const settingsSessionList = document.querySelector("#settings-session-list");
      const settingsProfileData = document.querySelector("#settings-profile-data");
      const demotionDialog = document.querySelector("#demotion-dialog");
      const demotionCancel = document.querySelector("#demotion-cancel");
      const sessionsSearch = document.querySelector("#sessions-search");
      const sessionsList = document.querySelector("#sessions-list");
      const sessionTitle = document.querySelector("#session-title");
      const sessionMeta = document.querySelector("#session-meta");
      const sessionTopicLink = document.querySelector("#session-topic-link");
      const sessionMessages = document.querySelector("#session-messages");
      const sessionSendForm = document.querySelector("#session-send-form");
      const sessionSendInput = document.querySelector("#session-send-input");
      const sessionSendButton = document.querySelector("#session-send-button");
      const sessionReadonlyNote = document.querySelector("#session-readonly-note");
      const requestStorageKey = "webAuthRequestId";
      let polling = false;
      let selectedUser = null;
      let selectedDatabaseUser = null;
      let selectedDashboardSession = null;
      let userOffset = 0;
      let userSearchTimer;
      let sessionSearchTimer;
      let loadedSessions = [];
      let currentSettingsSection = "history";
      const dashboardErrors = {
        authentication_required: "Сессия истекла. Обнови страницу и войди снова.",
        dashboard_timeout: "Сервер долго отвечает. Попробуй ещё раз.",
        dashboard_unavailable: "Не удалось загрузить данные. Попробуй ещё раз.",
        chat_or_message_not_found: "Чат или сообщение не найдено в истории бота.",
        user_not_found: "Пользователь с таким ID не найден.",
        session_not_found: "Сессия не найдена.",
        session_inactive: "Закрытая сессия доступна только для чтения.",
        session_topic_unavailable: "У этой сессии нет доступной темы Telegram.",
        self_management_not_allowed: "Нельзя менять собственные админ-права через панель.",
        insufficient_admin_level: "Недостаточно прав для изменения админ-уровня.",
        level_four_limit: "Администратор 4-го уровня может назначать только уровни 1–3.",
        prefix_management_requires_level_five: "Изменять префиксы может только администратор 5-го уровня.",
        target_is_not_admin: "Префиксы доступны только пользователям с админ-правами.",
        invalid_prefixes: "Можно выбрать не более 3 допустимых префиксов.",
        telegram_timeout: "Telegram не успел выполнить действие. Обнови историю перед повтором.",
        telegram_action_failed: "Telegram не выполнил действие. Возможно, пользователь заблокировал бота или сообщение недоступно.",
      };

      async function readApiResponse(response) {
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
          if (response.status === 501) {
            throw new Error("Сервер сайта устарел. Перезапусти бота и обнови страницу.");
          }
          throw new Error("Сервер сайта вернул неожиданный ответ. Перезапусти бота и попробуй снова.");
        }
        return response.json();
      }

      function dashboardErrorMessage(result, fallback) {
        return dashboardErrors[result.error] || result.error || fallback;
      }

      function showAccount(account) {
        const name = account.display_name || account.username || "Администратор";
        accountName.textContent = `Вы вошли как ${name}${account.username ? ` (@${account.username})` : ""}.`;
        profileDisplayName.textContent = name;
        profileUsername.textContent = account.username
          ? `@${account.username}`
          : `ID ${account.telegram_user_id}`;
        avatarFallback.textContent = name
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .map((part) => part[0])
          .join("")
          .toLocaleUpperCase("ru");
        profileAvatar.hidden = true;
        if (account.avatar_available) profileAvatar.src = "/api/auth/avatar";
        document.body.classList.add("authenticated");
        sessionStorage.removeItem(requestStorageKey);
        loginCard.hidden = true;
        homeCard.hidden = false;
        if (!terminalOutput.textContent) void runTerminalCommand("help");
      }

      function appendTerminalOutput(command, lines) {
        const prompt = document.createElement("span");
        prompt.className = "terminal-line";
        prompt.textContent = `> ${command}\n`;
        terminalOutput.append(prompt, document.createTextNode(`${lines.join("\n")}\n\n`));
        if (terminalOutput.textContent.length > 8000) {
          terminalOutput.textContent = terminalOutput.textContent.slice(-6000);
        }
        terminalOutput.scrollTop = terminalOutput.scrollHeight;
      }

      async function runTerminalCommand(command) {
        const normalizedCommand = command.trim().toLowerCase();
        if (!["help", "status", "logs"].includes(normalizedCommand)) {
          appendTerminalOutput(command, ["Команда недоступна. Введите help для списка безопасных команд."]);
          return;
        }
        try {
          const response = await fetch(
            `/api/dashboard/command?command=${encodeURIComponent(normalizedCommand)}`,
            { credentials: "same-origin", cache: "no-store" },
          );
          const result = await readApiResponse(response);
          if (!response.ok) throw new Error(result.error || "Команда не выполнена.");
          appendTerminalOutput(normalizedCommand, result.output);
        } catch (error) {
          appendTerminalOutput(normalizedCommand, [error.message || "Не удалось выполнить команду."]);
        }
      }

      async function postDashboardAction(payload) {
        const response = await fetch("/api/dashboard/messages", {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await readApiResponse(response);
        if (!response.ok) throw new Error(dashboardErrorMessage(result, "Действие не выполнено."));
        return result;
      }

      async function fetchDashboardJson(url, options = {}) {
        const response = await fetch(url, {
          credentials: "same-origin",
          cache: "no-store",
          ...options,
        });
        const result = await readApiResponse(response);
        if (!response.ok) throw new Error(dashboardErrorMessage(result, "Действие не выполнено."));
        return result;
      }

      async function postDashboardJson(url, payload) {
        return fetchDashboardJson(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }

      function renderActionHistory(container, actions, emptyText) {
        container.replaceChildren();
        if (!actions.length) {
          const empty = document.createElement("p");
          empty.className = "logs-empty";
          empty.textContent = emptyText;
          container.append(empty);
          return;
        }
        for (const action of actions) {
          const item = document.createElement("article");
          item.className = "history-item";
          const title = document.createElement("strong");
          title.textContent = action.action || "Действие";
          const details = document.createElement("p");
          details.textContent = action.details || "";
          const meta = document.createElement("small");
          meta.textContent = [
            action.created_at ? formatDate(action.created_at) : "",
            action.actor_id ? `Администратор ID ${action.actor_id}` : "",
          ].filter(Boolean).join(" · ");
          item.append(title, details, meta);
          container.append(item);
        }
      }

      function setSettingsSection(name) {
        currentSettingsSection = name;
        for (const section of settingsSections) {
          section.hidden = section.id !== `settings-${name}`;
        }
        for (const button of settingsNavigation) {
          if (button.dataset.settingsSection === name) {
            button.setAttribute("aria-current", "true");
          } else {
            button.removeAttribute("aria-current");
          }
        }
      }

      function renderPrefixOptions(user) {
        settingsPrefixList.replaceChildren();
        for (const option of user.prefix_options || []) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          const label = document.createElement("label");
          label.className = "prefix-option";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = option.value;
          checkbox.checked = (user.prefixes || []).includes(option.value);
          checkbox.disabled = !user.can_manage_prefixes;
          const text = document.createElement("span");
          text.textContent = option.label;
          label.append(checkbox, text);
          cell.append(label);
          row.append(cell);
          settingsPrefixList.append(row);
        }
        settingsSavePrefixes.disabled = !user.can_manage_prefixes;
        settingsSavePrefixes.title = user.can_manage_prefixes
          ? ""
          : "Для изменения префиксов нужен 5-й уровень админ-прав.";
      }

      function renderUserSettings(user, { preserveSection = false } = {}) {
        selectedDatabaseUser = user;
        settingsTitle.textContent = `Настройки · ${user.display_name} (ID ${user.user_id})`;
        settingsAdminStatus.textContent = user.has_admin_rights
          ? `Активные админ-права: уровень ${user.admin_level}${user.admin_rank ? ` · ${user.admin_rank}` : ""}.`
          : "Активных админ-прав нет.";
        settingsAdminLevel.value = String(Math.max(1, Math.min(5, user.admin_level || 1)));
        settingsAdminLevel.max = String(user.max_assignable_level || 3);
        if (Number(settingsAdminLevel.value) > Number(settingsAdminLevel.max)) {
          settingsAdminLevel.value = settingsAdminLevel.max;
        }
        settingsAdminLevelValue.textContent = settingsAdminLevel.value;
        settingsAdminLevel.disabled = !user.can_manage_levels;
        settingsChangeLevel.disabled = !user.can_manage_levels;
        settingsRemoveAdmin.hidden = !user.has_admin_rights;
        settingsRemoveAdmin.disabled = !user.can_manage_levels;
        settingsAdminMessage.textContent = "";
        settingsPrefixMessage.textContent = "";
        renderPrefixOptions(user);
        renderActionHistory(settingsHistoryList, user.history || [], "История действий пока пуста.");

        settingsSessionList.replaceChildren();
        if (!user.sessions || !user.sessions.length) {
          const empty = document.createElement("p");
          empty.className = "logs-empty";
          empty.textContent = "Сессий пока нет.";
          settingsSessionList.append(empty);
        } else {
          for (const session of user.sessions) {
            const button = document.createElement("button");
            button.className = "settings-session-row";
            button.type = "button";
            const status = session.status === "active" ? "Активная" : "Закрытая";
            button.textContent = `${status} · ${session.mood || "Без темы"} · ${formatDate(session.started_at)}`;
            button.dataset.sessionId = session.session_id;
            settingsSessionList.append(button);
          }
        }

        const profile = user.profile || {};
        const data = [
          ["Telegram ID", user.user_id],
          ["Username", user.username ? `@${user.username}` : "Не указан"],
          ["Имя", user.display_name],
          ["ID профиля в боте", profile.id_profile || "Не указан"],
          ["Дата регистрации", profile.registration_date ? formatDate(profile.registration_date) : "Не указана"],
          ["Уровень админ-прав", user.has_admin_rights ? user.admin_level : "Нет"],
          ["Префиксы", (user.prefix_labels || []).join(", ") || "Нет"],
          ["Последний админ-тег", profile.last_admin_tag || "Не указан"],
          ["Заблокирован в боте", user.is_banned ? "Да" : "Нет"],
          ["Заблокировал бота", user.blocked_bot ? "Да" : "Нет"],
        ];
        settingsProfileData.replaceChildren();
        for (const [label, value] of data) {
          const term = document.createElement("dt");
          term.textContent = label;
          const description = document.createElement("dd");
          description.textContent = String(value);
          settingsProfileData.append(term, description);
        }
        setSettingsSection(preserveSection ? currentSettingsSection : "history");
      }

      function formatDate(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime())
          ? String(value)
          : date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
      }

      databaseSearchForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const userId = databaseUserIdInput.value.trim();
        if (!/^\d{1,20}$/.test(userId)) {
          databaseStatus.textContent = "Введи корректный числовой Telegram ID.";
          databaseResult.hidden = true;
          return;
        }
        databaseStatus.textContent = "Ищем пользователя…";
        databaseResult.hidden = true;
        try {
          const { user } = await fetchDashboardJson(
            `/api/dashboard/database/user?user_id=${encodeURIComponent(userId)}`,
          );
          selectedDatabaseUser = user;
          databaseUserName.textContent = user.display_name;
          databaseUserIdentity.textContent = `${user.username ? `@${user.username} · ` : ""}ID ${user.user_id}`;
          databaseUserStatus.textContent = user.is_banned ? "Заблокирован в боте" : "Аккаунт найден";
          databaseUserStatus.classList.toggle("blocked", Boolean(user.is_banned));
          databaseBotBlockedStatus.textContent = user.blocked_bot
            ? "Пользователь заблокировал бота."
            : "Пользователь не блокировал бота.";
          if (user.avatar) {
            databaseAvatar.src = user.avatar;
            databaseAvatar.hidden = false;
            databaseAvatarFallback.hidden = true;
          } else {
            databaseAvatar.hidden = true;
            databaseAvatarFallback.hidden = false;
            databaseAvatarFallback.textContent = user.display_name
              .split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toLocaleUpperCase("ru");
          }
          databaseResult.hidden = false;
          databaseStatus.textContent = "";
        } catch (error) {
          databaseStatus.textContent = error.message || "Не удалось найти пользователя.";
        }
      });

      databaseAvatar.addEventListener("error", () => {
        databaseAvatar.hidden = true;
        databaseAvatarFallback.hidden = false;
      });

      databaseSettingsButton.addEventListener("click", () => {
        if (!selectedDatabaseUser) return;
        renderUserSettings(selectedDatabaseUser);
        userSettingsDialog.showModal();
      });

      settingsClose.addEventListener("click", () => userSettingsDialog.close());
      settingsDone.addEventListener("click", () => userSettingsDialog.close());
      settingsNavigation.forEach((button) => {
        button.addEventListener("click", () => setSettingsSection(button.dataset.settingsSection));
      });
      settingsAdminLevel.addEventListener("input", () => {
        settingsAdminLevelValue.textContent = settingsAdminLevel.value;
      });

      settingsChangeLevel.addEventListener("click", async () => {
        if (!selectedDatabaseUser) return;
        settingsChangeLevel.disabled = true;
        settingsAdminMessage.textContent = "Сохраняем уровень…";
        try {
          const result = await postDashboardJson("/api/dashboard/database/actions", {
            action: "change_level",
            user_id: selectedDatabaseUser.user_id,
            level: Number(settingsAdminLevel.value),
            send_briefing: settingsSendBriefing.checked,
          });
          await refreshDatabaseUser();
          settingsAdminMessage.textContent = result.warning || result.message || "Уровень изменён.";
        } catch (error) {
          settingsAdminMessage.textContent = error.message || "Не удалось изменить уровень.";
        } finally {
          settingsChangeLevel.disabled = !selectedDatabaseUser?.can_manage_levels;
        }
      });

      function openDemotionDialog() {
        if (typeof demotionDialog.showModal === "function") demotionDialog.showModal();
      }

      settingsRemoveAdmin.addEventListener("click", openDemotionDialog);
      demotionCancel.addEventListener("click", () => demotionDialog.close());
      demotionDialog.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-demotion-mode]");
        if (!button || !selectedDatabaseUser) return;
        const mode = button.dataset.demotionMode;
        button.disabled = true;
        try {
          const result = await postDashboardJson("/api/dashboard/database/actions", {
            action: "change_level",
            user_id: selectedDatabaseUser.user_id,
            level: 0,
            send_briefing: false,
            demotion_mode: mode,
          });
          demotionDialog.close();
          await refreshDatabaseUser();
          settingsAdminMessage.textContent = result.message || "Админ-права сняты.";
        } catch (error) {
          settingsAdminMessage.textContent = error.message || "Не удалось снять админ-права.";
        } finally {
          button.disabled = false;
        }
      });

      settingsPrefixList.addEventListener("change", (event) => {
        if (event.target.type !== "checkbox") return;
        const checked = settingsPrefixList.querySelectorAll("input:checked");
        if (checked.length > 3) {
          event.target.checked = false;
          settingsPrefixMessage.textContent = "Нельзя выбрать больше трёх префиксов.";
        } else {
          settingsPrefixMessage.textContent = "";
        }
      });

      settingsSavePrefixes.addEventListener("click", async () => {
        if (!selectedDatabaseUser) return;
        const prefixes = [...settingsPrefixList.querySelectorAll("input:checked")].map((input) => input.value);
        settingsSavePrefixes.disabled = true;
        settingsPrefixMessage.textContent = "Сохраняем префиксы…";
        try {
          const result = await postDashboardJson("/api/dashboard/database/actions", {
            action: "set_prefixes",
            user_id: selectedDatabaseUser.user_id,
            prefixes,
          });
          await refreshDatabaseUser();
          settingsPrefixMessage.textContent = result.warning || result.message || "Префиксы сохранены.";
        } catch (error) {
          settingsPrefixMessage.textContent = error.message || "Не удалось сохранить префиксы.";
        } finally {
          settingsSavePrefixes.disabled = !selectedDatabaseUser?.can_manage_prefixes;
        }
      });

      async function refreshDatabaseUser() {
        if (!selectedDatabaseUser) return;
        const userId = selectedDatabaseUser.user_id;
        const { user } = await fetchDashboardJson(
          `/api/dashboard/database/user?user_id=${encodeURIComponent(userId)}`,
        );
        selectedDatabaseUser = user;
        databaseUserName.textContent = user.display_name;
        databaseUserIdentity.textContent = `${user.username ? `@${user.username} · ` : ""}ID ${user.user_id}`;
        databaseUserStatus.textContent = user.is_banned ? "Заблокирован в боте" : "Аккаунт найден";
        databaseUserStatus.classList.toggle("blocked", Boolean(user.is_banned));
        databaseBotBlockedStatus.textContent = user.blocked_bot
          ? "Пользователь заблокировал бота."
          : "Пользователь не блокировал бота.";
        renderUserSettings(user, { preserveSection: true });
      }

      async function loadDashboardSessions() {
        sessionsList.replaceChildren();
        const loading = document.createElement("p");
        loading.className = "logs-empty";
        loading.textContent = "Загружаем сессии…";
        sessionsList.append(loading);
        try {
          const query = sessionsSearch.value.trim();
          const result = await fetchDashboardJson(
            `/api/dashboard/sessions?query=${encodeURIComponent(query)}`,
          );
          loadedSessions = result.sessions || [];
          renderDashboardSessions();
        } catch (error) {
          sessionsList.replaceChildren();
          const failed = document.createElement("p");
          failed.className = "logs-empty";
          failed.textContent = error.message || "Не удалось загрузить сессии.";
          sessionsList.append(failed);
        }
      }

      function renderDashboardSessions() {
        sessionsList.replaceChildren();
        if (!loadedSessions.length) {
          const empty = document.createElement("p");
          empty.className = "logs-empty";
          empty.textContent = "Сессий пока нет.";
          sessionsList.append(empty);
          return;
        }
        for (const session of loadedSessions) {
          const button = document.createElement("button");
          button.className = "session-row";
          button.type = "button";
          button.dataset.sessionId = session.session_id;
          button.setAttribute(
            "aria-current",
            selectedDashboardSession?.session_id === session.session_id ? "true" : "false",
          );
          const name = document.createElement("strong");
          name.textContent = session.user_name || `ID ${session.user_id}`;
          const meta = document.createElement("small");
          meta.textContent = `${session.status === "active" ? "Активная" : "Закрытая"} · ID ${session.user_id} · ${session.mood || "Без темы"}`;
          button.append(name, meta);
          sessionsList.append(button);
        }
      }

      async function openDashboardSession(session) {
        selectedDashboardSession = session;
        renderDashboardSessions();
        sessionTitle.textContent = `${session.user_name || `ID ${session.user_id}`} · ${session.mood || "Сессия"}`;
        sessionMeta.textContent = `${session.status === "active" ? "Активная" : "Закрытая"} · ID ${session.user_id} · Начата ${formatDate(session.started_at)}${session.closed_at ? ` · Закрыта ${formatDate(session.closed_at)}` : ""}`;
        sessionTopicLink.hidden = !session.topic_url;
        if (session.topic_url) sessionTopicLink.href = session.topic_url;
        const active = session.status === "active";
        sessionSendForm.hidden = !active;
        sessionReadonlyNote.textContent = active
          ? "Удалять можно только сохранённые сообщения активной сессии."
          : "Сессия закрыта и доступна только для чтения.";
        sessionMessages.replaceChildren();
        const loading = document.createElement("p");
        loading.className = "logs-empty";
        loading.textContent = "Загружаем сообщения…";
        sessionMessages.append(loading);
        try {
          const { messages } = await fetchDashboardJson(
            `/api/dashboard/sessions/${encodeURIComponent(session.session_id)}/messages`,
          );
          renderSessionMessages(messages || [], active);
        } catch (error) {
          sessionMessages.replaceChildren();
          const failed = document.createElement("p");
          failed.className = "logs-empty";
          failed.textContent = error.message || "Не удалось загрузить сообщения.";
          sessionMessages.append(failed);
        }
      }

      function renderSessionMessages(messages, active) {
        sessionMessages.replaceChildren();
        if (!messages.length) {
          const empty = document.createElement("p");
          empty.className = "logs-empty";
          empty.textContent = "Сохранённых сообщений для этой сессии нет.";
          sessionMessages.append(empty);
          return;
        }
        for (const message of messages) {
          const item = document.createElement("article");
          item.className = `session-message ${message.direction || "user"}`;
          item.dataset.messageId = message.message_id;
          const meta = document.createElement("small");
          meta.textContent = `${message.actor_name || message.direction || "Сообщение"} · ${formatDate(message.created_at)}`;
          const text = document.createElement("p");
          text.textContent = message.deleted
            ? "[сообщение удалено]"
            : message.text || message.caption || (message.message_type === "other" ? "[вложение]" : "");
          item.append(meta, text);
          if (active && !message.deleted) {
            const actions = document.createElement("div");
            actions.className = "message-actions";
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button";
            remove.textContent = "Удалить";
            remove.dataset.sessionMessageAction = "delete";
            actions.append(remove);
            item.append(actions);
          }
          sessionMessages.append(item);
        }
        sessionMessages.scrollTop = sessionMessages.scrollHeight;
      }

      sessionsSearch.addEventListener("input", () => {
        window.clearTimeout(sessionSearchTimer);
        sessionSearchTimer = window.setTimeout(() => void loadDashboardSessions(), 250);
      });

      sessionsList.addEventListener("click", (event) => {
        const button = event.target.closest("[data-session-id]");
        if (!button) return;
        const session = loadedSessions.find((item) => item.session_id === button.dataset.sessionId);
        if (session) void openDashboardSession(session);
      });

      settingsSessionList.addEventListener("click", (event) => {
        const button = event.target.closest("[data-session-id]");
        if (!button) return;
        const session = (selectedDatabaseUser?.sessions || []).find(
          (item) => item.session_id === button.dataset.sessionId,
        );
        if (!session) return;
        userSettingsDialog.close();
        const tab = dashboardTabs.find((item) => item.dataset.dashboardTab === "sessions");
        tab?.click();
        void loadDashboardSessions().then(() => {
          const found = loadedSessions.find((item) => item.session_id === session.session_id);
          if (found) void openDashboardSession(found);
        });
      });

      sessionSendForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!selectedDashboardSession || !sessionSendInput.value.trim()) return;
        sessionSendButton.disabled = true;
        try {
          await postDashboardJson("/api/dashboard/sessions/actions", {
            action: "send",
            session_id: selectedDashboardSession.session_id,
            text: sessionSendInput.value.trim(),
          });
          sessionSendInput.value = "";
          await openDashboardSession(selectedDashboardSession);
        } catch (error) {
          window.alert(error.message || "Не удалось отправить сообщение в сессию.");
        } finally {
          sessionSendButton.disabled = false;
        }
      });

      sessionMessages.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-session-message-action]");
        if (!button || !selectedDashboardSession || !window.confirm("Удалить сообщение в Telegram?")) return;
        const item = button.closest("[data-message-id]");
        if (!item) return;
        button.disabled = true;
        try {
          await postDashboardJson("/api/dashboard/sessions/actions", {
            action: "delete",
            session_id: selectedDashboardSession.session_id,
            message_id: item.dataset.messageId,
          });
          await openDashboardSession(selectedDashboardSession);
        } catch (error) {
          window.alert(error.message || "Не удалось удалить сообщение.");
          button.disabled = false;
        }
      });

      function renderUsers(users, append) {
        if (!append) logsUsers.replaceChildren();
        for (const user of users) {
          const button = document.createElement("button");
          button.className = "logs-user";
          button.type = "button";
          button.dataset.userId = user.user_id;
          button.setAttribute(
            "aria-current",
            selectedUser && selectedUser.user_id === user.user_id ? "true" : "false",
          );
          const name = document.createElement("strong");
          name.textContent = user.display_name;
          const identity = document.createElement("small");
          identity.textContent = user.username
            ? `@${user.username} · ID ${user.user_id}`
            : `ID ${user.user_id}`;
          button.append(name, identity);
          logsUsers.append(button);
        }
      }

      async function loadDashboardUsers({ append = false } = {}) {
        if (!append) userOffset = 0;
        const query = logsSearch.value.trim();
        logsUsers.setAttribute("aria-busy", "true");
        try {
          const response = await fetch(
            `/api/dashboard/users?query=${encodeURIComponent(query)}&offset=${userOffset}`,
            { credentials: "same-origin", cache: "no-store" },
          );
          const result = await readApiResponse(response);
          if (!response.ok) throw new Error(dashboardErrorMessage(result, "Не удалось загрузить пользователей."));
          renderUsers(result.users, append);
          userOffset += result.users.length;
          logsMore.hidden = result.users.length < 100;
          if (!logsUsers.childElementCount) {
            const empty = document.createElement("p");
            empty.className = "logs-empty";
            empty.textContent = query ? "Пользователи не найдены." : "Пока нет пользователей.";
            logsUsers.append(empty);
          }
        } catch (error) {
          if (!append) logsUsers.replaceChildren();
          const message = document.createElement("p");
          message.className = "logs-error";
          message.textContent = error.message || "Не удалось загрузить список.";
          logsUsers.append(message);
          logsMore.hidden = true;
        } finally {
          logsUsers.setAttribute("aria-busy", "false");
        }
      }

      function renderMessages(messages) {
        logsMessages.replaceChildren();
        if (!messages.length) {
          const empty = document.createElement("p");
          empty.className = "logs-empty";
          empty.textContent = "Для этого пользователя пока нет сообщений в истории.";
          logsMessages.append(empty);
          return;
        }

        for (const message of [...messages].reverse()) {
          const item = document.createElement("article");
          item.className = `message-item ${message.direction === "outgoing" ? "outgoing" : "incoming"}`;
          item.dataset.messageId = message.telegram_message_id || "";
          const body = document.createElement("p");
          body.className = "message-body";
          body.textContent = message.text || message.caption || `[${message.message_type || "сообщение"}]`;
          const meta = document.createElement("div");
          meta.className = "message-meta";
          const direction = document.createElement("span");
          direction.textContent = message.direction === "outgoing" ? "Бот" : "Пользователь";
          const date = document.createElement("time");
          date.textContent = message.created_at || "";
          meta.append(direction, date);
          item.append(body, meta);

          if (message.telegram_message_id && body.textContent !== "[удалено через панель]") {
            const actions = document.createElement("div");
            actions.className = "message-actions";
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "delete-message";
            remove.textContent = "Удалить";
            remove.dataset.messageAction = "delete";
            actions.append(remove);
            item.append(actions);
          }
          logsMessages.append(item);
        }
        logsMessages.scrollTop = logsMessages.scrollHeight;
      }

      async function openUserChat(user) {
        if (selectedUser && selectedUser.user_id !== user.user_id && historyDialog.open) {
          historyDialog.close();
        }
        selectedUser = user;
        logsChatName.textContent = user.display_name;
        logsChatId.textContent = user.username
          ? `@${user.username} · Telegram ID ${user.user_id}`
          : `Telegram ID ${user.user_id}`;
        logsSendInput.disabled = false;
        logsSendButton.disabled = false;
        logsHistoryButton.disabled = false;
        logsUsers.querySelectorAll("[data-user-id]").forEach((button) => {
          button.setAttribute("aria-current", button.dataset.userId === user.user_id ? "true" : "false");
        });
        logsMessages.replaceChildren();
        const loading = document.createElement("p");
        loading.className = "logs-empty";
        loading.textContent = "Загружаем историю…";
        logsMessages.append(loading);
        try {
          const response = await fetch(
            `/api/dashboard/users/${encodeURIComponent(user.user_id)}/messages`,
            { credentials: "same-origin", cache: "no-store" },
          );
          const result = await readApiResponse(response);
          if (!response.ok) throw new Error(dashboardErrorMessage(result, "Не удалось загрузить историю."));
          if (selectedUser && selectedUser.user_id === user.user_id) renderMessages(result.messages);
        } catch (error) {
          logsMessages.replaceChildren();
          const message = document.createElement("p");
          message.className = "logs-error";
          message.textContent = error.message || "Не удалось загрузить историю.";
          logsMessages.append(message);
        }
      }

      async function loadUserActions(user) {
        historyList.replaceChildren();
        const loading = document.createElement("p");
        loading.className = "logs-empty";
        loading.textContent = "Загружаем историю…";
        historyList.append(loading);
        try {
          const response = await fetch(
            `/api/dashboard/users/${encodeURIComponent(user.user_id)}/actions`,
            { credentials: "same-origin", cache: "no-store" },
          );
          const result = await readApiResponse(response);
          if (!response.ok) throw new Error(dashboardErrorMessage(result, "Не удалось загрузить историю действий."));
          if (!selectedUser || selectedUser.user_id !== user.user_id) return;
          historyList.replaceChildren();
          if (!result.actions.length) {
            const empty = document.createElement("p");
            empty.className = "logs-empty";
            empty.textContent = "Действий пока нет.";
            historyList.append(empty);
            return;
          }
          for (const action of result.actions) {
            const item = document.createElement("article");
            item.className = "history-item";
            const title = document.createElement("strong");
            title.textContent = action.action;
            const meta = document.createElement("small");
            const date = action.created_at ? new Date(action.created_at).toLocaleString("ru-RU") : "";
            meta.textContent = `${date}${action.actor_id ? ` · администратор ID ${action.actor_id}` : ""}`;
            item.append(title, meta);
            if (action.details) {
              const details = document.createElement("p");
              details.textContent = action.details;
              item.append(details);
            }
            historyList.append(item);
          }
        } catch (error) {
          historyList.replaceChildren();
          const message = document.createElement("p");
          message.className = "logs-error";
          message.textContent = error.message || "Не удалось загрузить историю действий.";
          historyList.append(message);
        }
      }

      async function loadSession() {
        const response = await fetch("/api/auth/session", { credentials: "same-origin", cache: "no-store" });
        const result = await readApiResponse(response);
        if (!response.ok) throw new Error(result.error || "Не удалось проверить вход.");
        if (result.authenticated) showAccount(result.account);
      }

      async function pollLogin() {
        if (polling) return;
        polling = true;
        try {
          const requestId = sessionStorage.getItem(requestStorageKey);
          if (!requestId || document.hidden) return;
          const response = await fetch(`/api/auth/status?id=${encodeURIComponent(requestId)}`, {
            credentials: "same-origin",
            cache: "no-store",
          });
          const result = await readApiResponse(response);
          if (!response.ok) throw new Error(result.error || "Не удалось проверить статус входа.");
          if (result.status === "approved" && result.account) {
            showAccount(result.account);
            statusText.textContent = "";
            verification.hidden = true;
          } else if (result.status === "denied" || result.status === "expired") {
            sessionStorage.removeItem(requestStorageKey);
            verification.hidden = true;
            statusText.textContent = result.status === "denied"
              ? "Вход отклонён. Можно начать заново."
              : "Срок запроса истёк. Начни вход заново.";
          } else if (result.status === "pending") {
            statusText.textContent = "Открой Telegram, подтверди запрос у бота и вернись сюда.";
          }
        } catch {
          statusText.textContent = "Не удалось проверить статус. Проверь соединение и попробуй снова.";
        } finally {
          polling = false;
          if (sessionStorage.getItem(requestStorageKey) && !document.hidden) {
            window.setTimeout(pollLogin, 2500);
          }
        }
      }

      loginButton.addEventListener("click", async () => {
        loginButton.disabled = true;
        statusText.textContent = "Создаём защищённый запрос…";
        try {
          const response = await fetch("/api/auth/start", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
          });
          const result = await readApiResponse(response);
          if (!response.ok) {
            if (response.status === 429) throw new Error("Слишком много попыток. Подожди минуту и попробуй снова.");
            throw new Error(result.error || "Не удалось создать запрос входа.");
          }
          sessionStorage.setItem(requestStorageKey, result.request_id);
          verificationCode.textContent = result.verification_code;
          verification.hidden = false;
          statusText.textContent = "Сверь код в Telegram и подтверди вход у бота.";
          window.location.assign(result.bot_url);
        } catch (error) {
          statusText.textContent = error.message || "Не удалось открыть Telegram.";
          loginButton.disabled = false;
        }
      });

      profileToggle.addEventListener("click", () => {
        const isExpanded = profileToggle.getAttribute("aria-expanded") === "true";
        profileToggle.setAttribute("aria-expanded", String(!isExpanded));
        profileMenu.hidden = isExpanded;
      });

      document.addEventListener("click", (event) => {
        if (!event.target.closest(".home-topbar")) {
          profileMenu.hidden = true;
          profileToggle.setAttribute("aria-expanded", "false");
        }
      });

      profileAvatar.addEventListener("load", () => {
        profileAvatar.hidden = false;
      });

      profileAvatar.addEventListener("error", () => {
        profileAvatar.hidden = true;
      });

      terminalForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const command = terminalInput.value;
        if (!command.trim()) return;
        terminalInput.value = "";
        void runTerminalCommand(command);
      });

      dashboardTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
          const selectedTab = tab.dataset.dashboardTab;
          dashboardTabs.forEach((item) => {
            item.setAttribute("aria-selected", String(item === tab));
          });
          consoleView.hidden = selectedTab !== "console";
          logsView.hidden = selectedTab !== "logs";
          databaseView.hidden = selectedTab !== "database";
          sessionsView.hidden = selectedTab !== "sessions";
          if (selectedTab === "logs" && !logsUsers.childElementCount) void loadDashboardUsers();
          if (selectedTab === "sessions") void loadDashboardSessions();
        });
      });

      logsSearch.addEventListener("input", () => {
        window.clearTimeout(userSearchTimer);
        userSearchTimer = window.setTimeout(() => loadDashboardUsers(), 250);
      });

      logsUsers.addEventListener("click", (event) => {
        const button = event.target.closest("[data-user-id]");
        if (!button) return;
        const userId = button.dataset.userId;
        const identity = button.querySelector("small").textContent;
        void openUserChat({
          user_id: userId,
          display_name: button.querySelector("strong").textContent,
          username: identity.startsWith("@") ? identity.split(" · ")[0].slice(1) : null,
        });
      });

      logsMore.addEventListener("click", () => {
        void loadDashboardUsers({ append: true });
      });

      logsSendForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!selectedUser || !logsSendInput.value.trim()) return;
        const text = logsSendInput.value.trim();
        logsSendButton.disabled = true;
        try {
          await postDashboardAction({
            action: "send",
            user_id: selectedUser.user_id,
            text,
          });
          logsSendInput.value = "";
          await openUserChat(selectedUser);
        } catch (error) {
          window.alert(error.message || "Не удалось отправить сообщение.");
        } finally {
          logsSendButton.disabled = !selectedUser;
        }
      });

      logsMessages.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-message-action]");
        if (!button || !selectedUser) return;
        const item = button.closest("[data-message-id]");
        const messageId = item && item.dataset.messageId;
        if (!messageId) return;
        const action = button.dataset.messageAction;
        if (action !== "delete" || !window.confirm("Удалить это сообщение в Telegram?")) return;
        button.disabled = true;
        try {
          await postDashboardAction({
            action,
            user_id: selectedUser.user_id,
            message_id: messageId,
          });
          await openUserChat(selectedUser);
        } catch (error) {
          window.alert(error.message || "Не удалось удалить сообщение.");
          button.disabled = false;
        }
      });

      logsHistoryButton.addEventListener("click", () => {
        if (!selectedUser) return;
        historyTitle.textContent = `Последние действия · ${selectedUser.display_name}`;
        historyDialog.showModal();
        void loadUserActions(selectedUser);
      });

      historyClose.addEventListener("click", () => historyDialog.close());

      logoutButton.addEventListener("click", async () => {
        logoutButton.disabled = true;
        try {
          const response = await fetch("/api/auth/logout", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
          });
          if (!response.ok) throw new Error("Не удалось выйти из панели.");
          homeCard.hidden = true;
          document.body.classList.remove("authenticated");
          profileMenu.hidden = true;
          profileToggle.setAttribute("aria-expanded", "false");
          selectedUser = null;
          logsSendInput.disabled = true;
          logsSendButton.disabled = true;
          logsHistoryButton.disabled = true;
          if (historyDialog.open) historyDialog.close();
          logsUsers.replaceChildren();
          logsMessages.replaceChildren();
          loginCard.hidden = false;
          loginButton.disabled = false;
          statusText.textContent = "Вы вышли из панели.";
        } catch (error) {
          appendTerminalOutput("logout", [error.message || "Не удалось выйти из панели."]);
        } finally {
          logoutButton.disabled = false;
        }
      });

      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) pollLogin();
      });
      window.addEventListener("pageshow", () => pollLogin());

      loadSession()
        .then(() => pollLogin())
        .catch(() => {
          statusText.textContent = "Не удалось проверить состояние входа.";
        });