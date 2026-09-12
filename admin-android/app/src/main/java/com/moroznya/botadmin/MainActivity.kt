package com.moroznya.botadmin

import android.content.Context
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private val apiBaseUrl = "https://YOUR-SERVER.example.com"
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var root: LinearLayout
    private lateinit var content: LinearLayout
    private lateinit var status: TextView
    private val prefs by lazy { getSharedPreferences("admin_session", Context.MODE_PRIVATE) }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        if (prefs.getString("token", null) == null) showOwnerLogin() else showUsers()
    }

    private fun baseLayout(title: String): LinearLayout {
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 20, 24, 16)
            setBackgroundColor(Color.rgb(245, 247, 250))
        }
        val bar = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        val heading = TextView(this).apply {
            text = title
            textSize = 24f
            setTextColor(Color.rgb(20, 30, 45))
        }
        bar.addView(heading, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(bar)
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 24, 0, 0)
        }
        root.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        status = TextView(this).apply { setTextColor(Color.DKGRAY) }
        root.addView(status)
        setContentView(root)
        return root
    }

    private fun showOwnerLogin() {
        baseLayout("Вход владельца")
        val hint = TextView(this).apply {
            this.text = "Введите ID владельца, чтобы продолжить"
            textSize = 16f
        }
        val input = EditText(this).apply {
            this.hint = "ID владельца"
            inputType = 2
        }
        val next = button("Далее")
        content.addView(hint)
        content.addView(input, fieldParams())
        content.addView(next, LinearLayout.LayoutParams(-1, -2).apply {
            gravity = Gravity.BOTTOM or Gravity.END
        })
        next.isEnabled = false
        next.setBackgroundColor(Color.GRAY)
        input.addTextChangedListener(SimpleTextWatcher { valid ->
            next.isEnabled = valid
            next.setBackgroundColor(if (valid) Color.rgb(25, 118, 210) else Color.GRAY)
        })
        next.setOnClickListener {
            if (input.text.toString() == "7545068007") showPasswordLogin()
            else status.text = "Неверный ID владельца"
        }
    }

    private fun showPasswordLogin() {
        baseLayout("Пароль администратора")
        val input = EditText(this).apply {
            hint = "Пароль"
            inputType = 0x81
        }
        val next = button("Далее")
        next.isEnabled = false
        next.setBackgroundColor(Color.GRAY)
        content.addView(TextView(this).apply { text = "Введите пароль администратора"; textSize = 16f })
        content.addView(input, fieldParams())
        content.addView(next)
        input.addTextChangedListener(SimpleTextWatcher { valid ->
            next.isEnabled = valid
            next.setBackgroundColor(if (valid) Color.rgb(25, 118, 210) else Color.GRAY)
        })
        next.setOnClickListener { login(input.text.toString()) }
    }

    private fun login(password: String) {
        status.text = "Проверка..."
        request("POST", "/api/auth/login", null, JSONObject()
            .put("owner_id", "7545068007").put("password", password)) { code, body ->
            runOnUiThread {
                if (code == 200) {
                    prefs.edit().putString("token", JSONObject(body).getString("access_token")).apply()
                    showUsers()
                } else status.text = "Неверный пароль или сервер недоступен"
            }
        }
    }

    private fun showUsers() {
        baseLayout("Пользователи бота")
        val logout = button("Выйти")
        (root.getChildAt(0) as LinearLayout).addView(logout)
        logout.setOnClickListener { prefs.edit().clear().apply(); showOwnerLogin() }
        status.text = "Загрузка..."
        request("GET", "/api/users?limit=500", prefs.getString("token", null), null) { code, body ->
            runOnUiThread {
                if (code != 200) {
                    status.text = "Не удалось загрузить пользователей"
                    return@runOnUiThread
                }
                status.text = ""
                val items = JSONObject(body).optJSONArray("items") ?: JSONArray()
                for (i in 0 until items.length()) addUserCard(items.getJSONObject(i))
            }
        }
    }

    private fun addUserCard(item: JSONObject) {
        val profile = item.optJSONObject("profile") ?: JSONObject()
        val id = item.optString("telegram_id")
        val name = profile.optString("username").ifBlank {
            profile.optString("first_name").ifBlank { "Пользователь $id" }
        }
        val card = Button(this).apply {
            text = "$name\nID: $id   Сообщений: ${item.optInt("message_count")}"
            textSize = 16f
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            setPadding(20, 18, 20, 18)
            setOnClickListener { showMessages(id, name) }
        }
        content.addView(card, LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, 0, 0, 12) })
    }

    private fun showMessages(userId: String, name: String) {
        baseLayout(name)
        val back = button("← Назад")
        (root.getChildAt(0) as LinearLayout).addView(back)
        back.setOnClickListener { showUsers() }
        status.text = "Загрузка истории..."
        request("GET", "/api/users/$userId/messages?limit=500", prefs.getString("token", null), null) { code, body ->
            runOnUiThread {
                if (code != 200) { status.text = "Не удалось загрузить историю"; return@runOnUiThread }
                status.text = ""
                val items = JSONObject(body).optJSONArray("items") ?: JSONArray()
                for (i in items.length() - 1 downTo 0) {
                    val message = items.getJSONObject(i)
                    val direction = if (message.optString("direction") == "incoming") "Пользователь" else "Бот"
                    val text = message.optString("text").ifBlank { message.optString("caption", "[${message.optString("message_type")}]") }
                    content.addView(TextView(this).apply {
                        this.text = "$direction\n$text\n${message.optString("created_at")}"
                        textSize = 16f
                        setPadding(16, 12, 16, 12)
                        setTextColor(if (direction == "Бот") Color.rgb(25, 90, 160) else Color.DKGRAY)
                    })
                }
            }
        }
    }

    private fun request(method: String, path: String, token: String?, body: JSONObject?, done: (Int, String) -> Unit) {
        executor.execute {
            try {
                val connection = URL(apiBaseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
                connection.requestMethod = method
                connection.connectTimeout = 10000
                connection.readTimeout = 10000
                connection.setRequestProperty("Accept", "application/json")
                if (token != null) connection.setRequestProperty("X-Admin-Token", token)
                if (body != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.outputStream.use { it.write(body.toString().toByteArray()) }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                done(code, stream?.bufferedReader()?.use { it.readText() } ?: "")
            } catch (error: Exception) {
                done(0, error.message ?: "")
            }
        }
    }

    private fun button(text: String) = Button(this).apply { this.text = text; isAllCaps = false }
    private fun fieldParams() = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, 12, 0, 20) }
}

private class SimpleTextWatcher(private val callback: (Boolean) -> Unit) : android.text.TextWatcher {
    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = callback(!s.isNullOrBlank())
    override fun afterTextChanged(s: android.text.Editable?) = Unit
}
