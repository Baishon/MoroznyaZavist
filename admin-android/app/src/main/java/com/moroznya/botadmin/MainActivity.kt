package com.moroznya.botadmin

import android.content.Context
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import android.view.Window
import android.view.WindowInsets
import android.view.WindowInsetsController
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private val apiBaseUrl = "https://explained-sandwich-homeland-chemicals.trycloudflare.com"
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var root: FrameLayout
    private lateinit var content: LinearLayout
    private lateinit var status: TextView
    private val prefs by lazy { getSharedPreferences("admin_session", Context.MODE_PRIVATE) }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.setStatusBarColor(Color.TRANSPARENT)
        window.setNavigationBarColor(Color.TRANSPARENT)
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            window.insetsController?.let {
                it.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        }
        if (prefs.getString("token", null) == null) showOwnerLogin() else showUsers()
    }

    private fun baseLayout(title: String): LinearLayout {
        root = FrameLayout(this).apply {
            setBackgroundColor(Color.rgb(239, 243, 250))
        }
        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 20, 24, 16)
        }
        val bar = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        val heading = TextView(this).apply {
            this.text = title
            textSize = 24f
            setTextColor(Color.rgb(24, 35, 55))
        }
        bar.addView(heading, LinearLayout.LayoutParams(0, -2, 1f))
        page.addView(bar)
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 24, 0, 0)
        }
        page.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        status = TextView(this).apply { setTextColor(Color.DKGRAY) }
        page.addView(status)
        root.addView(page, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)
        root.alpha = 0f
        root.animate().alpha(1f).setDuration(220).start()
        return page
    }

    private fun showOwnerLogin() {
        baseLayout("Вход владельца")
        content.gravity = Gravity.CENTER
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
            if (input.text.toString() == "7545068007") transitionTo { showPasswordLogin() }
            else status.text = "Неверный ID владельца"
        }
    }

    private fun showPasswordLogin() {
        baseLayout("Пароль администратора")
        content.gravity = Gravity.CENTER
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
        val page = root.getChildAt(0) as LinearLayout
        val bar = page.getChildAt(0) as LinearLayout
        val menu = button("☰").apply {
            textSize = 22f
            setTextColor(Color.rgb(70, 90, 130))
            setBackgroundColor(Color.TRANSPARENT)
        }
        bar.addView(menu, 56, 56)
        menu.setOnClickListener { showProfileDrawer() }
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
        val page = root.getChildAt(0) as LinearLayout
        val back = button("← Назад")
        (page.getChildAt(0) as LinearLayout).addView(back)
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

    private fun transitionTo(next: () -> Unit) {
        root.animate().alpha(0f).setDuration(160).withEndAction { next() }.start()
    }

    private fun showProfileDrawer() {
        val scrim = View(this).apply {
            setBackgroundColor(Color.argb(90, 0, 0, 0))
            setOnClickListener { hideProfileDrawer(this) }
        }
        val drawer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 42, 22, 24)
            setBackgroundColor(Color.rgb(27, 38, 55))
            elevation = 18f
        }
        drawer.addView(TextView(this).apply {
            this.text = "Профиль администратора"
            textSize = 21f
            setTextColor(Color.WHITE)
        })
        drawer.addView(TextView(this).apply {
            this.text = "\nID владельца: 7545068007\nСтатус: авторизован"
            textSize = 16f
            setTextColor(Color.rgb(205, 218, 238))
        })
        root.addView(scrim, FrameLayout.LayoutParams(-1, -1))
        val params = FrameLayout.LayoutParams((resources.displayMetrics.widthPixels * 0.82).toInt(), -1)
        params.gravity = Gravity.START
        root.addView(drawer, params)
        drawer.translationX = -params.width.toFloat()
        drawer.animate().translationX(0f).setDuration(260).start()
    }

    private fun hideProfileDrawer(scrim: View) {
        val drawer = root.getChildAt(root.childCount - 1)
        drawer.animate().translationX(-drawer.width.toFloat()).setDuration(200).withEndAction {
            root.removeView(drawer)
            root.removeView(scrim)
        }.start()
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
