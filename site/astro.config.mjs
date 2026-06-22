// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
	integrations: [
		starlight({
			title: 'ja-media-toolkit Docs',
			customCss: ['./src/styles/custom.css'],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/EndlessReform/ja-media-toolkit' }],
			sidebar: [
				{
					label: 'Setup',
					items: [
						{ label: 'CLI Tools', link: '/setup/tools/' },
						{ label: 'Services', link: '/setup/services/' },
						{ label: 'Audiobookshelf', link: '/setup/audiobookshelf/' },
						{ label: 'Configuration', link: '/setup/config/' },
						{ label: 'Monitoring', link: '/setup/monitoring/' },
					],
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
