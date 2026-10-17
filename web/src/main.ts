import '@fontsource/roboto/400.css';
import '@fontsource/roboto/500.css';
import { mount } from 'svelte';
import App from './App.svelte';

export default mount(App, { target: document.getElementById('app')! });
