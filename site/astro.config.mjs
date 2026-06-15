// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
	integrations: [
		starlight({
			title: 'ja-media-toolkit Docs',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/EndlessReform/ja-media-toolkit' }],
			sidebar: [
				{
					label: 'Setup',
					items: [{ autogenerate: { directory: 'setup' } }],
				},
				{
					label: 'Guides',
					items: [{ autogenerate: { directory: 'guides' } }],
				},
				{
					label: 'Reference',
					items: [{ autogenerate: { directory: 'reference' } }],
				},
			],
		}),
	],
});
