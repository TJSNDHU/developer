<template>
  <div class="wrap" v-if="me">
    <h1>Welcome, {{ me.name || me.email }}</h1>
    <p>Your app is running. Extend <code>ui/src/</code> with your components.</p>
    <button @click="logout">Sign out</button>
  </div>

  <div class="wrap" v-else>
    <h1>{{ mode === 'login' ? 'Sign in' : 'Create account' }}</h1>
    <form @submit.prevent="submit">
      <input v-model="email" type="email" placeholder="Email" required />
      <input v-model="pw" type="password" placeholder="Password (≥ 8 chars)" required />
      <p v-if="err" class="err">{{ err }}</p>
      <button type="submit">{{ mode === 'login' ? 'Sign in' : 'Sign up' }}</button>
    </form>
    <p>
      <a href="#" @click.prevent="mode = mode === 'login' ? 'signup' : 'login'">
        {{ mode === 'login' ? "Don't have an account? Sign up" : 'Already have one? Sign in' }}
      </a>
    </p>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
const API = import.meta.env.VITE_API_URL || 'http://localhost:3001';
const me    = ref(null);
const mode  = ref('login');
const email = ref('');
const pw    = ref('');
const err   = ref('');

onMounted(async () => {
  const r = await fetch(`${API}/api/auth/me`, { credentials: 'include' });
  if (r.ok) me.value = await r.json();
});

async function submit() {
  err.value = '';
  const r = await fetch(`${API}/api/auth/${mode.value}`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: email.value, password: pw.value }),
  });
  const j = await r.json();
  if (!r.ok) return (err.value = j.error || 'Something went wrong.');
  me.value = j;
}

async function logout() {
  await fetch(`${API}/api/auth/logout`, { method: 'POST', credentials: 'include' });
  me.value = null;
}
</script>

<style>
.wrap { max-width: 420px; margin: 80px auto; font-family: system-ui; padding: 24px; }
input, button { display: block; width: 100%; padding: 10px; margin: 8px 0; }
.err { color: crimson; }
</style>
