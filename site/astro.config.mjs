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
						{ label: 'Lakehouse', link: '/setup/lakehouse/' },
						{ label: 'Operator Workbench', link: '/setup/operator-workbench/' },
					],
				},
				{
					label: 'Guides',
					items: [{ autogenerate: { directory: 'guides' } }],
				},
				{
					label: 'Services',
					items: [
						{ label: 'Overview', link: '/guides/services/' },
						{ autogenerate: { directory: 'services' } },
					],
				},
				{
					label: 'Reference',
					items: [{ autogenerate: { directory: 'reference' } }],
				},
			],
		}),
	],
});
